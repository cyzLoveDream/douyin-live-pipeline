"""Multi-signal 高能检测: RMS z-score, spectral flux, VAD, scene, keywords, chat."""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from dylive.config import AppConfig, DetectConfig
from dylive.exceptions import MediaError
from dylive.media import duration_seconds, extract_pcm_s16le, list_media, require_ffmpeg
from dylive.record import concat_recordings
from dylive.state import latest_job, read_json, recording_dir, write_json
from dylive.transcribe import Transcript, Word

log = logging.getLogger("dylive.detect")

SAMPLE_RATE = 8000


@dataclass
class Highlight:
    start: float
    end: float
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0
    why: dict[str, float] = field(default_factory=dict)
    title: str = ""
    hashtags: list[str] = field(default_factory=list)
    hook: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Event:
    t: float
    reason: str
    weight: float = 1.0
    span: tuple[float, float] | None = None


@dataclass
class FrameSeries:
    times: np.ndarray
    rms: np.ndarray
    energy_z: np.ndarray
    flux: np.ndarray
    speech: np.ndarray
    hop: float


def detect_media(
    cfg: AppConfig,
    media: Path,
    extra_events: list[Event] | None = None,
    transcript: Transcript | None = None,
) -> list[Highlight]:
    if not media.is_file():
        raise MediaError(f"找不到媒体文件: {media}")
    dur = duration_seconds(media)
    if dur <= 0:
        raise MediaError(f"无法读取时长: {media}")
    frames = analyze_audio(media, hop=cfg.detect.audio_window_seconds)
    scenes = [e.t for e in scene_cut_events(media, cfg.detect)]
    chat = load_chat_events(cfg.detect.chat_events_file)
    chat.extend(extra_events or [])
    highlights = score_and_select(
        frames,
        dur,
        cfg.detect,
        transcript=transcript,
        scene_times=scenes,
        chat_events=chat,
    )
    log.info("高能片段 %s 个（多信号加权）时长 %.1fs", len(highlights), dur)
    for h in highlights:
        log.info(
            "  %.1f-%.1fs score=%.2f why=%s",
            h.start,
            h.end,
            h.score,
            {k: round(v, 3) for k, v in h.why.items()},
        )
    return highlights


def detect_job(
    cfg: AppConfig,
    source: str | Path | None = None,
    *,
    transcriber: Any = None,
    transcript: Transcript | None = None,
) -> tuple[Path, list[Highlight]]:
    media, job_key = resolve_media(cfg, source)
    if transcript is None:
        from dylive.transcribe import ensure_transcript

        transcript = ensure_transcript(cfg, media, job_key, transcriber=transcriber)
    highlights = detect_media(cfg, media, transcript=transcript)
    from dylive.polish import polish_highlights

    highlights = polish_highlights(highlights, transcript, room_id=job_key)
    payload = {
        "media": str(media),
        "transcript": str(cfg.paths.data / "jobs" / job_key / "transcript.json"),
        "highlights": [
            {
                "start": h.start,
                "end": h.end,
                "score": h.score,
                "reasons": h.reasons,
                "why": {k: round(float(v), 4) for k, v in h.why.items()},
                "title": h.title,
                "hashtags": h.hashtags,
                "hook": h.hook,
            }
            for h in highlights
        ],
    }
    dest_dir = cfg.paths.data / "jobs" / job_key
    dest_dir.mkdir(parents=True, exist_ok=True)
    write_json(dest_dir / "highlights.json", payload)
    return media, highlights


def highlight_from_dict(row: dict[str, Any]) -> Highlight:
    why_raw = row.get("why") or {}
    why = {str(k): float(v) for k, v in why_raw.items()} if isinstance(why_raw, dict) else {}
    tags = row.get("hashtags") or []
    if isinstance(tags, str):
        tags = [tags]
    return Highlight(
        start=float(row["start"]),
        end=float(row["end"]),
        reasons=list(row.get("reasons") or []),
        score=float(row.get("score") or 0),
        why=why,
        title=str(row.get("title") or ""),
        hashtags=[str(t) for t in tags],
        hook=str(row.get("hook") or ""),
    )


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


def analyze_audio(media: Path, hop: float = 0.25, *, sample_rate: int = SAMPLE_RATE) -> FrameSeries:
    pcm = extract_pcm_s16le(media, sample_rate=sample_rate)
    samples = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype=np.int16)
    return analyze_samples(samples, sample_rate, hop)


def analyze_samples(samples: np.ndarray, sample_rate: int, hop: float) -> FrameSeries:
    arr = np.asarray(samples)
    if arr.dtype == np.int16:
        arr = arr.astype(np.float32) / 32768.0
    else:
        arr = arr.astype(np.float32)
        peak = float(np.max(np.abs(arr)) or 1.0)
        if peak > 1.5:
            arr = arr / 32768.0
    hop = max(0.05, float(hop))
    win = max(16, int(sample_rate * hop))
    step = win  # non-overlapping hops, 200–250ms
    if len(arr) < win:
        rms = np.array([float(np.sqrt(np.mean(arr**2))) if len(arr) else 0.0], dtype=np.float32)
        return FrameSeries(
            times=np.array([0.0], dtype=np.float32),
            rms=rms,
            energy_z=np.zeros_like(rms),
            flux=np.zeros_like(rms),
            speech=np.array([1.0 if rms[0] > 0.02 else 0.0], dtype=np.float32),
            hop=hop,
        )
    window = np.hanning(win).astype(np.float32)
    rms_list: list[float] = []
    flux_list: list[float] = []
    times: list[float] = []
    prev_mag: np.ndarray | None = None
    for i in range(0, len(arr) - win + 1, step):
        frame = arr[i : i + win]
        rms_list.append(float(np.sqrt(np.mean(frame**2))))
        spec = np.abs(np.fft.rfft(frame * window))
        if prev_mag is None:
            flux_list.append(0.0)
        else:
            diff = spec - prev_mag
            flux_list.append(float(np.linalg.norm(np.maximum(diff, 0.0))))
        prev_mag = spec
        times.append(i / float(sample_rate))
    rms = np.asarray(rms_list, dtype=np.float32)
    flux_raw = np.asarray(flux_list, dtype=np.float32)
    mu = float(rms.mean())
    sigma = float(rms.std()) or 1e-6
    energy_z = (rms - mu) / sigma
    p95 = float(np.percentile(flux_raw, 95)) or 1e-6
    flux = np.clip(flux_raw / p95, 0.0, 2.5)
    abs_floor = 0.012
    vad_thresh = max(float(np.percentile(rms, 25)), 0.08 * float(rms.max() or 1.0), abs_floor)
    speech = (rms >= vad_thresh).astype(np.float32)
    return FrameSeries(
        times=np.asarray(times, dtype=np.float32),
        rms=rms,
        energy_z=energy_z,
        flux=flux,
        speech=speech,
        hop=hop,
    )


def audio_energy_events(media: Path, detect: DetectConfig, *, duration: float) -> list[Event]:
    frames = analyze_audio(media, hop=detect.audio_window_seconds)
    if len(frames.times) == 0:
        return []
    events: list[Event] = []
    active_start: float | None = None
    last_t = 0.0
    for t, z in zip(frames.times, frames.energy_z):
        last_t = float(t)
        if float(z) >= 1.0:
            if active_start is None:
                active_start = float(t)
        elif active_start is not None:
            events.append(
                Event(
                    t=(active_start + float(t)) / 2,
                    reason="audio",
                    weight=1.0,
                    span=(active_start, float(t) + detect.audio_window_seconds),
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
    log.debug("音频尖峰 %s 个", len(events))
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


def score_window(
    start: float,
    end: float,
    frames: FrameSeries,
    detect: DetectConfig,
    *,
    transcript: Transcript | None,
    scene_times: list[float],
    chat_events: list[Event],
) -> tuple[float, dict[str, float]]:
    if end <= start:
        return 0.0, {k: 0.0 for k in ("energy", "flux", "speech", "scene", "keywords", "chat")}
    mask = (frames.times >= start) & (frames.times < end)
    if not np.any(mask):
        # nearest hop
        idx = int(np.argmin(np.abs(frames.times - start))) if len(frames.times) else 0
        mask = np.zeros(len(frames.times), dtype=bool)
        if len(mask):
            mask[idx] = True
    energy = float(np.mean(np.maximum(frames.energy_z[mask], 0.0))) if np.any(mask) else 0.0
    flux = float(np.mean(frames.flux[mask])) if np.any(mask) else 0.0
    speech = float(np.mean(frames.speech[mask])) if np.any(mask) else 0.0
    scene_n = sum(1 for t in scene_times if start <= t < end)
    scene = min(1.5, 0.7 * scene_n)
    hits = keyword_hits(transcript, start, end, detect.keywords) if transcript else []
    keywords = float(min(4, len(hits)))
    chat = float(sum(ev.weight for ev in chat_events if start <= ev.t < end))
    # Dead air: energy/flux collapse; keywords and chat still count (买它 in a quiet region).
    if speech < 0.2:
        energy *= 0.12
        flux *= 0.12
    w = detect.weights
    why = {
        "energy": energy * w.energy,
        "flux": flux * w.flux,
        "speech": speech * w.speech,
        "scene": scene * w.scene,
        "keywords": keywords * w.keywords,
        "chat": chat * w.chat,
    }
    return float(sum(why.values())), why


def keyword_hits(
    transcript: Transcript | None, start: float, end: float, keywords: list[str]
) -> list[str]:
    if not transcript or not keywords:
        return []
    text = "".join(
        w.word for w in transcript.words if w.end > start and w.start < end
    )
    for seg in transcript.segments:
        if seg.end > start and seg.start < end:
            text += seg.text or ""
    return [kw for kw in keywords if kw and kw in text]


def score_and_select(
    frames: FrameSeries,
    duration: float,
    detect: DetectConfig,
    *,
    transcript: Transcript | None = None,
    scene_times: list[float] | None = None,
    chat_events: list[Event] | None = None,
) -> list[Highlight]:
    scene_times = list(scene_times or [])
    chat_events = list(chat_events or [])
    if duration <= 0 or len(frames.times) == 0:
        return []

    def scored(start: float, end: float) -> Highlight:
        sc, why = score_window(
            start,
            end,
            frames,
            detect,
            transcript=transcript,
            scene_times=scene_times,
            chat_events=chat_events,
        )
        reasons = [k for k, v in why.items() if v > 0.05]
        return Highlight(start=start, end=end, reasons=reasons or ["energy"], score=sc, why=why)

    candidates: list[tuple[float, float]] = []
    interest = (
        detect.weights.energy * np.maximum(frames.energy_z, 0.0)
        + detect.weights.flux * frames.flux
        + detect.weights.speech * frames.speech
    )
    smoothed = _smooth(interest, 3)
    floor = float(max(smoothed.mean() + 0.2 * (smoothed.std() or 1.0), 0.25))
    for pi in _local_maxima(smoothed, floor):
        left = right = pi
        peak = float(smoothed[pi])
        while left > 0 and float(smoothed[left - 1]) > 0.35 * peak:
            left -= 1
        while right + 1 < len(smoothed) and float(smoothed[right + 1]) > 0.35 * peak:
            right += 1
        start = max(0.0, float(frames.times[left]) - detect.pad_before_seconds)
        end = min(duration, float(frames.times[right]) + frames.hop + detect.pad_after_seconds)
        candidates.append((start, end))

    words = transcript.words if transcript else []
    for w in words:
        joined = w.word
        if any(kw and kw in joined for kw in detect.keywords):
            candidates.append(
                (
                    max(0.0, w.start - detect.pad_before_seconds),
                    min(duration, w.end + max(detect.min_clip_seconds * 0.6, 2.0)),
                )
            )
    if transcript:
        for seg in transcript.segments:
            if any(kw and kw in (seg.text or "") for kw in detect.keywords):
                candidates.append(
                    (
                        max(0.0, seg.start - detect.pad_before_seconds),
                        min(duration, seg.end + detect.pad_after_seconds + detect.min_clip_seconds * 0.4),
                    )
                )
    for t in scene_times:
        candidates.append(
            (max(0.0, t - detect.pad_before_seconds), min(duration, t + detect.min_clip_seconds))
        )
    for ev in chat_events:
        t0, t1 = (ev.span if ev.span else (ev.t, ev.t))
        candidates.append(
            (
                max(0.0, t0 - detect.pad_before_seconds),
                min(duration, t1 + detect.min_clip_seconds),
            )
        )

    if not candidates:
        pi = int(np.argmax(interest))
        t = float(frames.times[pi])
        candidates.append(
            (max(0.0, t - detect.pad_before_seconds), min(duration, t + detect.min_clip_seconds))
        )

    raw: list[Highlight] = []
    for start, end in candidates:
        if end - start < detect.min_clip_seconds:
            missing = detect.min_clip_seconds - (end - start)
            start = max(0.0, start - missing / 2)
            end = min(duration, start + detect.min_clip_seconds)
            if end - start < detect.min_clip_seconds:
                start = max(0.0, end - detect.min_clip_seconds)
        if end - start > detect.max_clip_seconds + 0.05:
            start, end = _best_subwindow(start, end, detect.max_clip_seconds, scored)
        raw.append(scored(start, end))

    merged = _merge_highlights(raw, detect.merge_gap_seconds, duration, detect, scored)
    snapped: list[Highlight] = []
    for h in merged:
        s, e = snap_window(h.start, h.end, words, max_delta=detect.snap_max_seconds, duration=duration)
        if e - s < detect.min_clip_seconds:
            missing = detect.min_clip_seconds - (e - s)
            s = max(0.0, s - missing / 2)
            e = min(duration, s + detect.min_clip_seconds)
        if e - s > detect.max_clip_seconds + 0.05:
            s, e = _best_subwindow(s, e, detect.max_clip_seconds, scored)
        snapped.append(scored(round(s, 3), round(e, 3)))

    snapped.sort(key=lambda h: h.score, reverse=True)
    picked: list[Highlight] = []
    for h in snapped:
        if h.end - h.start < 0.4:
            continue
        overlap = False
        for p in picked:
            inter = min(h.end, p.end) - max(h.start, p.start)
            if inter > 0.5 * min(h.duration, p.duration):
                overlap = True
                break
        if not overlap:
            picked.append(h)
        if len(picked) >= detect.max_clips:
            break
    picked.sort(key=lambda h: h.start)
    return picked


def snap_window(
    start: float,
    end: float,
    words: list[Word],
    *,
    max_delta: float = 0.35,
    duration: float | None = None,
) -> tuple[float, float]:
    """Snap edges to nearest word/sentence boundary within ±max_delta (default 0.15–0.4s)."""
    if not words:
        return start, end
    max_delta = min(0.4, max(0.15, max_delta))
    starts = [w.start for w in words]
    ends = [w.end for w in words]
    sent_ends = [w.end for w in words if any(c in (w.word or "") for c in "。！？!?")]
    sent_starts: list[float] = []
    for i, w in enumerate(words[:-1]):
        if any(c in (w.word or "") for c in "。！？!?"):
            sent_starts.append(words[i + 1].start)

    def nearest(t: float, points: list[float]) -> float:
        best, best_d = t, max_delta + 1
        for p in points:
            d = abs(p - t)
            if d <= max_delta and d < best_d:
                best, best_d = p, d
        return best

    # Prefer sentence boundaries when they are as close as a raw word edge.
    new_start = nearest(start, sent_starts + starts)
    new_end = nearest(end, sent_ends + ends)
    if duration is not None:
        new_start = min(max(0.0, new_start), duration)
        new_end = min(max(0.0, new_end), duration)
    if new_end <= new_start + 0.2:
        return start, end
    return new_start, new_end


def merge_windows(events: list[Event], detect: DetectConfig, *, duration: float) -> list[Highlight]:
    """Merge nearby events into clips in [min, max] seconds. Kept for unit tests / extras."""
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
        if h.duration < detect.min_clip_seconds:
            missing = detect.min_clip_seconds - h.duration
            h.start = max(0.0, h.start - missing / 2)
            h.end = min(duration, h.start + detect.min_clip_seconds)
            if h.end - h.start < detect.min_clip_seconds:
                h.start = max(0.0, h.end - detect.min_clip_seconds)
        if h.duration > detect.max_clip_seconds + 0.05:
            t = h.start
            while t < h.end - 0.05:
                chunk_end = min(h.end, t + detect.max_clip_seconds)
                if chunk_end - t < detect.min_clip_seconds and out:
                    prev = out[-1]
                    room = detect.max_clip_seconds - prev.duration
                    if room > 0:
                        prev.end = min(h.end, prev.end + room)
                    break
                out.append(Highlight(start=t, end=chunk_end, reasons=list(h.reasons), score=h.score))
                t = chunk_end
        else:
            h.start = round(h.start, 3)
            h.end = round(h.end, 3)
            out.append(h)

    if not out and events:
        out.append(Highlight(start=0.0, end=round(duration, 3), reasons=["full"], score=1.0))
    cleaned: list[Highlight] = []
    for h in out:
        h.start = max(0.0, round(h.start, 3))
        h.end = min(duration, round(h.end, 3))
        if h.end - h.start < 0.4:
            continue
        cleaned.append(h)
    return cleaned


def _merge_highlights(
    raw: list[Highlight],
    gap: float,
    duration: float,
    detect: DetectConfig,
    scored,
) -> list[Highlight]:
    items = sorted(raw, key=lambda h: h.start)
    merged: list[Highlight] = []
    for h in items:
        if not merged:
            merged.append(h)
            continue
        prev = merged[-1]
        if h.start - prev.end <= gap:
            start = prev.start
            end = max(prev.end, h.end)
            if end - start > detect.max_clip_seconds + 0.05:
                # keep the higher-scoring one; don't swallow a distant peak
                if h.score > prev.score:
                    merged[-1] = h
                continue
            merged[-1] = scored(start, min(duration, end))
        else:
            merged.append(h)
    return merged


def _best_subwindow(start: float, end: float, max_len: float, scored) -> tuple[float, float]:
    if end - start <= max_len:
        return start, end
    best_s, best_sc = start, -1.0
    step = 0.25
    t = start
    while t + max_len <= end + 1e-6:
        h = scored(t, t + max_len)
        if h.score > best_sc:
            best_s, best_sc = t, h.score
        t += step
    return best_s, best_s + max_len


def _smooth(x: np.ndarray, k: int = 3) -> np.ndarray:
    if len(x) == 0:
        return x
    k = min(k, len(x))
    kernel = np.ones(k) / k
    return np.convolve(x, kernel, mode="same")


def _local_maxima(x: np.ndarray, min_val: float) -> list[int]:
    peaks: list[int] = []
    for i in range(len(x)):
        if float(x[i]) < min_val:
            continue
        left = float(x[i - 1]) if i else -math.inf
        right = float(x[i + 1]) if i + 1 < len(x) else -math.inf
        if float(x[i]) >= left and float(x[i]) >= right:
            peaks.append(i)
    return peaks
