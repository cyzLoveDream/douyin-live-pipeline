"""ffmpeg 二次创作: 9:16, loudness, title card, 来源字幕, optional whisper."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from dylive.config import AppConfig
from dylive.detect import Highlight
from dylive.exceptions import MediaError
from dylive.media import duration_seconds, find_font, require_ffmpeg, video_size
from dylive.state import latest_job, read_json, write_json

log = logging.getLogger("dylive.edit")


def edit_job(
    cfg: AppConfig,
    source: str | Path | None = None,
    *,
    title: str | None = None,
    room_id: str | None = None,
) -> list[Path]:
    cfg.paths.ensure()
    media, highlights, job_key = _load_highlights(cfg, source)
    room_id = room_id or job_key

    if not highlights:
        log.warning("没有高能片段，导出整段（最长 max_clip）")
        dur = duration_seconds(media)
        end = min(dur, cfg.detect.max_clip_seconds) if dur else 0
        if end <= 0:
            raise MediaError(f"媒体无法剪辑: {media}")
        highlights = [Highlight(start=0.0, end=end, reasons=["full"], score=0)]

    outputs: list[Path] = []
    for i, h in enumerate(highlights, start=1):
        out = cfg.paths.output / f"{room_id or media.stem}_{i:02d}_{int(h.start)}-{int(h.end)}.mp4"
        clip_title = title or (f"{room_id} 高能" if room_id else f"高能 {i}")
        render_clip(cfg, media, h, out, title=clip_title, room_id=room_id)
        outputs.append(out)
        log.info("写出 %s (%.1fs)", out, h.duration)

    write_json(
        cfg.paths.data / "jobs" / (room_id or media.stem) / "edit.json",
        {"clips": [str(p) for p in outputs], "media": str(media)},
    )
    return outputs


def _load_highlights(
    cfg: AppConfig, source: str | Path | None
) -> tuple[Path, list[Highlight], str]:
    if source:
        path = Path(source)
        if path.is_file() and path.suffix.lower() == ".json":
            return _from_highlights_json(path)
        job_json = cfg.paths.data / "jobs" / str(source) / "highlights.json"
        if path.is_dir() and (path / "highlights.json").is_file():
            return _from_highlights_json(path / "highlights.json")
        if job_json.is_file():
            return _from_highlights_json(job_json)

    job = latest_job(cfg)
    if job and (job / "highlights.json").is_file():
        return _from_highlights_json(job / "highlights.json")

    from dylive.detect import detect_job

    media, highlights = detect_job(cfg, source)
    return media, highlights, media.parent.name


def _from_highlights_json(path: Path) -> tuple[Path, list[Highlight], str]:
    payload = read_json(path)
    media = Path(payload["media"])
    highlights = [Highlight(**row) for row in payload.get("highlights") or []]
    return media, highlights, path.parent.name


def render_clip(
    cfg: AppConfig,
    media: Path,
    highlight: Highlight,
    dest: Path,
    *,
    title: str,
    room_id: str | None,
) -> Path:
    ffmpeg = require_ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, highlight.start)
    dur = max(0.2, highlight.end - highlight.start)
    font = find_font()
    caption = ""
    if cfg.edit.source_caption and room_id:
        caption = f"来源 live.douyin.com/{room_id}"
    elif cfg.edit.source_caption:
        caption = "来源 抖音直播"

    srt_path = None
    if cfg.edit.whisper:
        srt_path = _maybe_whisper(cfg, media, start, dur, dest.with_suffix(".srt"))

    vgraph, uses_complex = _video_graph(cfg, media, caption=caption, font=font, srt=srt_path)
    body = dest.with_suffix(".body.mp4")
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{dur:.3f}",
        "-i",
        str(media),
    ]
    if uses_complex:
        args += ["-filter_complex", vgraph, "-map", "[vout]", "-map", "0:a?"]
    else:
        args += ["-vf", vgraph]
    args += [
        "-af",
        f"loudnorm=I={cfg.edit.loudness_i}:TP=-1.5:LRA=11",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y",
        str(body),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=max(120, dur * 8), check=False)
    if proc.returncode != 0 or not body.is_file():
        raise MediaError(f"剪辑失败: {(proc.stderr or '')[-1500:]}")

    if cfg.edit.title_card:
        card = dest.with_suffix(".card.mp4")
        _make_title_card(cfg, card, title=title, font=font)
        _concat_two(card, body, dest)
        card.unlink(missing_ok=True)
        body.unlink(missing_ok=True)
    else:
        body.replace(dest)
    return dest


def _video_graph(
    cfg: AppConfig,
    media: Path,
    *,
    caption: str,
    font: str | None,
    srt: Path | None,
) -> tuple[str, bool]:
    base, complex_graph = _vertical_core(cfg, media)
    extras: list[str] = []
    if caption and font:
        extras.append(
            "drawtext=fontfile='{font}':text='{text}':fontcolor=white:"
            "fontsize=28:x=(w-text_w)/2:y=h-80:box=1:boxcolor=black@0.45:boxborderw=8".format(
                font=font, text=_drawtext_escape(caption)
            )
        )
    elif caption and not font:
        log.warning("找不到中文字体，跳过来源字幕。可安装 fonts-noto-cjk")
    if srt and srt.is_file():
        srt_escaped = str(srt).replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")
        extras.append(f"subtitles='{srt_escaped}'")
    if not complex_graph:
        return ",".join([base] + extras), False
    graph = base
    if extras:
        graph += f"[vmid];[vmid]{','.join(extras)}[vout]"
    else:
        graph += "[vout]"
    return graph, True


def _vertical_core(cfg: AppConfig, media: Path) -> tuple[str, bool]:
    w, h = cfg.edit.width, cfg.edit.height
    src_w, src_h = video_size(media)
    target_ratio = w / h
    if cfg.edit.fill == "crop":
        if src_w > 0 and src_h > 0 and (src_w / src_h) > target_ratio:
            return f"crop=ih*{target_ratio}:ih,scale={w}:{h}", False
        return f"crop=iw:iw/{target_ratio},scale={w}:{h}", False
    # blur-fill
    return (
        f"[0:v]split[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:10[blur];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fit];"
        f"[blur][fit]overlay=(W-w)/2:(H-h)/2",
        True,
    )


def _make_title_card(cfg: AppConfig, dest: Path, *, title: str, font: str | None) -> None:
    ffmpeg = require_ffmpeg()
    w, h = cfg.edit.width, cfg.edit.height
    dur = cfg.edit.title_card_seconds
    text = _drawtext_escape(title[:32] or "高能切片")
    color = f"color=c=0x101018:s={w}x{h}:d={dur:.2f}"
    if font:
        color = (
            f"{color},drawtext=fontfile='{font}':text='{text}':fontcolor=white:"
            f"fontsize=64:x=(w-text_w)/2:y=(h-text_h)/2"
        )
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        color,
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=44100:cl=stereo:d={dur:.2f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        "-y",
        str(dest),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise MediaError(f"标题卡失败: {(proc.stderr or '')[-800:]}")


def _concat_two(a: Path, b: Path, dest: Path) -> None:
    ffmpeg = require_ffmpeg()
    # Normalize fps / pixel format / sample rate — loudnorm often switches audio to 48 kHz.
    graph = (
        "[0:v]fps=25,format=yuv420p,setsar=1[v0];"
        "[1:v]fps=25,format=yuv420p,setsar=1[v1];"
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];"
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(a),
        "-i",
        str(b),
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y",
        str(dest),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=180, check=False)
    if proc.returncode != 0:
        raise MediaError(f"拼接标题卡失败: {(proc.stderr or '')[-800:]}")


def _maybe_whisper(cfg: AppConfig, media: Path, start: float, dur: float, srt: Path) -> Path | None:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        log.info("faster-whisper 未安装，跳过字幕（pip install 'dylive[whisper]'）")
        return None
    ffmpeg = require_ffmpeg()
    wav = srt.with_suffix(".wav")
    cut = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{dur:.3f}",
        "-i",
        str(media),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-y",
        str(wav),
    ]
    subprocess.run(cut, check=False, capture_output=True, timeout=60)
    if not wav.is_file():
        return None
    try:
        model = WhisperModel(cfg.edit.whisper_model, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(wav), language="zh")
        lines = []
        for i, seg in enumerate(segments, start=1):
            lines.append(f"{i}\n{_ts(seg.start)} --> {_ts(seg.end)}\n{seg.text.strip()}\n")
        srt.write_text("\n".join(lines), encoding="utf-8")
        log.info("whisper 字幕 %s", srt)
        return srt
    except Exception as exc:  # noqa: BLE001
        log.info("whisper 不可用 (%s)，跳过字幕", exc)
        return None
    finally:
        wav.unlink(missing_ok=True)


def _ts(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _drawtext_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("%", "\\%")
        .replace(",", "\\,")
    )
