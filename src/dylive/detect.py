"""Find 高能场面: audio energy spikes, scene cuts, optional chat/gift events."""

from __future__ import annotations

import array
import json
import logging
import math
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from dylive.config import AppConfig, DetectConfig
from dylive.exceptions import MediaError
from dylive.media import duration_seconds, extract_pcm_s16le, list_media, require_ffmpeg
from dylive.record import concat_recordings
from dylive.state import latest_job, read_json, recording_dir, write_json

log = logging.getLogger("dylive.detect")


@dataclass
class Highlight:
    start: float
    end: float
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Event:
    t: float
    reason: str
    weight: float = 1.0
    span: tuple[float, float] | None = None


def detect_media(cfg: AppConfig, media: Path, extra_events: list[Event] | None = None) -> list[Highlight]:
    if not media.is_file():
        raise MediaError(f"找不到媒体文件: {media}")
    dur = duration_seconds(media)
    if dur <= 0:
        raise MediaError(f"无法读取时长: {media}")
    events: list[Event] = []
    events.extend(audio_energy_events(media, cfg.detect, duration=dur))
    events.extend(scene_cut_events(media, cfg.detect))
    events.extend(extra_events or [])
    events.extend(load_chat_events(cfg.detect.chat_events_file))
    log.info("事件 %s 个（音频/切镜/弹幕）时长 %.1fs", len(events), dur)
    highlights = merge_windows(events, cfg.detect, duration=dur)
    log.info("合并后高能片段 %s 个", len(highlights))
    return highlights


def detect_job(cfg: AppConfig, source: str | Path | None = None) -> tuple[Path, list[Highlight]]:
    media, job_key = resolve_media(cfg, source)
    highlights = detect_media(cfg, media)
    payload = {
        "media": str(media),
        "highlights": [asdict(h) for h in highlights],
    }
    dest_dir = cfg.paths.data / "jobs" / job_key
    dest_dir.mkdir(parents=True, exist_ok=True)
    write_json(dest_dir / "highlights.json", payload)
    return media, highlights


def resolve_media(cfg: AppConfig, source: str | Path | None) -> tuple[Path, str]:
    if source:
        path = Path(source)
        if path.is_file():
            return path, path.parent.name if path.parent.name else path.stem
        if path.is_dir():
            files = list_media(path)
            if not files:
                raise MediaError(f"目录里没有录像: {path}")
            merged = path / "_detect_concat.mp4"
            return concat_recordings(files, merged), path.name
        # treat as room key
        rec = recording_dir(cfg, str(source))
        files = list_media(rec)
        if not files:
            raise MediaError(f"没有录像: {rec}")
        merged = rec / "_detect_concat.mp4"
        return concat_recordings(files, merged), rec.name

    job = latest_job(cfg)
    if job:
        rec_json = job / "record.json"
        if rec_json.is_file():
            data = read_json(rec_json)
            files = [Path(f) for f in data.get("files") or [] if Path(f).is_file()]
            if files:
                merged = Path(files[0]).parent / "_detect_concat.mp4"
                return concat_recordings(files, merged), job.name
        rec = recording_dir(cfg, job.name)
        files = list_media(rec)
        if files:
            merged = rec / "_detect_concat.mp4"
            return concat_recordings(files, merged), job.name
    rec_root = cfg.paths.recordings
    rooms = [p for p in rec_root.iterdir() if p.is_dir()] if rec_root.exists() else []
    if not rooms:
        raise MediaError("没有可检测的录像。先运行 dylive record <url>")
    latest = max(rooms, key=lambda p: p.stat().st_mtime)
    files = list_media(latest)
    if not files:
        raise MediaError(f"没有录像: {latest}")
    merged = latest / "_detect_concat.mp4"
    return concat_recordings(files, merged), latest.name


def audio_energy_events(media: Path, detect: DetectConfig, *, duration: float) -> list[Event]:
    pcm = extract_pcm_s16le(media, sample_rate=8000)
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return []
    win = max(1, int(8000 * detect.audio_window_seconds))
    hop = max(1, win // 2)
    rms_series: list[tuple[float, float]] = []
    for i in range(0, len(samples) - win + 1, hop):
        chunk = samples[i : i + win]
        acc = 0
        for x in chunk:
            acc += int(x) * int(x)
        rms = math.sqrt(acc / len(chunk))
        t = i / 8000.0
        rms_series.append((t, rms))
    if not rms_series:
        return []
    values = sorted(r for _, r in rms_series)
    idx = min(len(values) - 1, max(0, int(len(values) * detect.audio_percentile / 100.0)))
    thresh = max(values[idx], 1.0)
    # Ignore near-silence even if percentile is low (flat audio).
    median = values[len(values) // 2]
    if thresh < median * 1.4 and thresh < 200:
        # No real spikes.
        return []
    events: list[Event] = []
    active_start: float | None = None
    last_t = 0.0
    for t, rms in rms_series:
        last_t = t
        if rms >= thresh:
            if active_start is None:
                active_start = t
        elif active_start is not None:
            events.append(
                Event(
                    t=(active_start + t) / 2,
                    reason="audio",
                    weight=1.0,
                    span=(active_start, t + detect.audio_window_seconds),
                )
            )
            active_start = None
    if active_start is not None:
        events.append(
            Event(
                t=(active_start + last_t) / 2,
                reason="audio",
                weight=1.0,
                span=(active_start, min(duration, last_t + detect.audio_window_seconds)),
            )
        )
    log.debug("音频尖峰 %s 个 (thresh=%.1f)", len(events), thresh)
    return events


def scene_cut_events(media: Path, detect: DetectConfig) -> list[Event]:
    ffmpeg = require_ffmpeg()
    filt = f"select='gt(scene,{detect.scene_threshold})',showinfo"
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(media),
            "-filter:v",
            filt,
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=max(60, duration_seconds(media) + 30),
        check=False,
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    events: list[Event] = []
    for match in re.finditer(r"pts_time:\s*([0-9.]+)", text):
        t = float(match.group(1))
        events.append(Event(t=t, reason="scene", weight=0.8))
    log.debug("切镜 %s 个", len(events))
    return events


def load_chat_events(path: Path | None) -> list[Event]:
    if not path or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("无法解析 chat_events_file: %s", exc)
        return []
    events: list[Event] = []
    rows: Iterable[Any]
    if isinstance(data, dict):
        rows = data.get("events") or data.get("items") or []
    else:
        rows = data
    for row in rows:
        if not isinstance(row, dict):
            continue
        t = row.get("t", row.get("time", row.get("ts")))
        try:
            t_f = float(t)
        except (TypeError, ValueError):
            continue
        kind = str(row.get("type") or "chat")
        weight = float(row.get("weight") or (2.0 if kind == "gift" else 1.0))
        events.append(Event(t=t_f, reason=kind, weight=weight))
    log.info("从 %s 读到 %s 条弹幕/礼物事件（本地文件，非私有 API）", path, len(events))
    return events


def merge_windows(events: list[Event], detect: DetectConfig, *, duration: float) -> list[Highlight]:
    """Merge nearby events into clips in [min, max] seconds."""
    if duration <= 0:
        return []
    raw: list[Highlight] = []
    for ev in events:
        if ev.span:
            start, end = ev.span
        else:
            start, end = ev.t, ev.t
        start = max(0.0, start - detect.pad_before_seconds)
        end = min(duration, end + detect.pad_after_seconds)
        if end <= start:
            end = min(duration, start + 0.4)
        raw.append(Highlight(start=start, end=end, reasons=[ev.reason], score=ev.weight))
    raw.sort(key=lambda h: h.start)
    merged: list[Highlight] = []
    for h in raw:
        if not merged:
            merged.append(h)
            continue
        prev = merged[-1]
        if h.start - prev.end <= detect.merge_gap_seconds:
            prev.end = max(prev.end, h.end)
            prev.score += h.score
            for r in h.reasons:
                if r not in prev.reasons:
                    prev.reasons.append(r)
        else:
            merged.append(h)

    out: list[Highlight] = []
    for h in merged:
        # Expand short windows around the center.
        if h.duration < detect.min_clip_seconds:
            missing = detect.min_clip_seconds - h.duration
            h.start = max(0.0, h.start - missing / 2)
            h.end = min(duration, h.start + detect.min_clip_seconds)
            if h.end - h.start < detect.min_clip_seconds:
                h.start = max(0.0, h.end - detect.min_clip_seconds)
        # Split over-long windows.
        if h.duration > detect.max_clip_seconds + 0.05:
            t = h.start
            while t < h.end - 0.05:
                chunk_end = min(h.end, t + detect.max_clip_seconds)
                if chunk_end - t < detect.min_clip_seconds and out:
                    # leftover shorter than min: extend previous if possible
                    prev = out[-1]
                    room = detect.max_clip_seconds - prev.duration
                    if room > 0:
                        prev.end = min(h.end, prev.end + room)
                    break
                out.append(
                    Highlight(start=t, end=chunk_end, reasons=list(h.reasons), score=h.score)
                )
                t = chunk_end
        else:
            h.start = round(h.start, 3)
            h.end = round(h.end, 3)
            out.append(h)

    # If the whole media is shorter than min_clip, keep one full-file clip when we saw events.
    if not out and events:
        out.append(
            Highlight(start=0.0, end=round(duration, 3), reasons=["full"], score=1.0)
        )
    # Dedup tiny overlaps after rounding
    cleaned: list[Highlight] = []
    for h in out:
        h.start = max(0.0, round(h.start, 3))
        h.end = min(duration, round(h.end, 3))
        if h.end - h.start < 0.4:
            continue
        cleaned.append(h)
    return cleaned
