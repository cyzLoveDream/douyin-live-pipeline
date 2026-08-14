"""ffmpeg 二次创作: timeline 编译, 特效预设, 强制烧录词级 ASS 字幕."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from dylive.captions import ffmpeg_subtitles_filter, first_sentence, slice_words, write_ass
from dylive.config import AppConfig
from dylive.detect import Highlight, highlight_from_dict
from dylive.effects import (
    build_filter_complex,
    fallback_specs,
    jumpcut_plan,
    keyword_pops,
    loudest_interval,
    plan_is_identity,
    remap_words,
    resolve_style,
    silence_gaps,
    warped_duration,
    xfade_concat,
)
from dylive.exceptions import MediaError
from dylive.jianying import export_jianying
from dylive.media import duration_seconds, require_cjk_font, require_ffmpeg
from dylive.polish import polish_highlights
from dylive.state import latest_job, read_json, write_json
from dylive.timeline import build_clip_timeline, build_job_timeline, save_timeline, timeline_path
from dylive.transcribe import Transcript, Word, ensure_transcript, load_transcript, transcript_path

log = logging.getLogger("dylive.edit")


def edit_job(
    cfg: AppConfig,
    source: str | Path | None = None,
    *,
    title: str | None = None,
    room_id: str | None = None,
    transcript: Transcript | None = None,
    transcriber=None,
    only: int | None = None,
) -> list[Path]:
    cfg.paths.ensure()
    media, highlights, job_key = _load_highlights(cfg, source)
    room_id = room_id or job_key
    if transcript is None:
        tr_file = transcript_path(cfg, job_key)
        if tr_file.is_file():
            transcript = load_transcript(tr_file)
        else:
            transcript = ensure_transcript(cfg, media, job_key, transcriber=transcriber)
    if not transcript.words:
        raise MediaError("没有词级字幕，请先运行 dylive transcribe")

    if not highlights:
        log.warning("没有高能片段，导出整段（最长 max_clip）")
        dur = duration_seconds(media)
        end = min(dur, cfg.detect.max_clip_seconds) if dur else 0
        if end <= 0:
            raise MediaError(f"媒体无法剪辑: {media}")
        highlights = [Highlight(start=0.0, end=end, reasons=["full"], score=0)]

    highlights = polish_highlights(highlights, transcript, room_id=room_id)

    from dylive.create import load_create, mix_voiceover

    create_data = load_create(cfg, job_key)
    cta = (create_data or {}).get("cta") or None
    vo_by_index: dict[int, dict] = {}
    for k, row in enumerate((create_data or {}).get("clips") or []):
        if isinstance(row, dict):
            vo_by_index[int(row.get("index", k))] = row

    styles = cfg.create.versions if cfg.create.versions else [cfg.edit.style]
    multi = len(styles) > 1
    base_style = cfg.edit.style

    from dylive.director import load_director

    director = load_director(cfg, job_key)
    directives: dict[int, dict] = {}
    if director:
        for row in director.get("clips") or []:
            if isinstance(row, dict):
                directives[int(row.get("index", len(directives)))] = row

    job_tl = build_job_timeline(cfg, media, highlights, transcript, room_id=room_id)
    save_timeline(timeline_path(cfg, job_key), job_tl)

    outputs: list[Path] = []
    all_words: list[Word] = []
    if only is not None:
        if not (0 <= only < len(highlights)):
            raise MediaError(f"片段索引 {only} 越界，共 {len(highlights)} 条")
        items = [(only, highlights[only])]
    else:
        items = list(enumerate(highlights))
    for orig_i, h in items:
        i = orig_i + 1
        clip_title = title or h.title or (f"{room_id} 高能" if room_id else f"高能 {i}")
        d = directives.get(orig_i) or {}
        d_style = d.get("style")
        d_effects = d.get("effects") or {}
        d_caption = d.get("caption_style")
        d_cta = d.get("cta")
        loop_styles = [d_style] if d_style else styles
        loop_multi = len(loop_styles) > 1
        for style in loop_styles:
            cfg.edit.style = style
            if loop_multi:
                out = cfg.paths.output / f"{room_id or media.stem}_{i:02d}_{style}_{int(h.start)}-{int(h.end)}.mp4"
            else:
                out = cfg.paths.output / f"{room_id or media.stem}_{i:02d}_{int(h.start)}-{int(h.end)}.mp4"
            render_clip(
                cfg,
                media,
                h,
                out,
                title=clip_title,
                room_id=room_id,
                transcript=transcript,
                cta=d_cta if d_cta else (cta if cfg.create.cta else None),
                filler_cut=cfg.create.filler_cut,
                effects=d_effects,
                caption_style=d_caption,
            )
            outputs.append(out)
            log.info("写出 %s (%.1fs)", out, h.duration)
            row = vo_by_index.get(orig_i) or vo_by_index.get(i)
            voice = (row or {}).get("voice")
            if cfg.create.voiceover and voice and Path(str(voice)).is_file():
                try:
                    vo_out = out.with_name(out.stem + "_vo.mp4")
                    mix_voiceover(out, Path(str(voice)), vo_out)
                    outputs.append(vo_out)
                    log.info("解说版 %s", vo_out)
                except Exception as exc:  # noqa: BLE001
                    log.warning("配音混音跳过: %s", exc)
        all_words.extend(slice_words(transcript.words, h.start, h.end, origin=h.start))

    cfg.edit.style = base_style

    if only is None:
        try:
            jy = export_jianying(
                cfg,
                room_id or job_key,
                outputs,
                words=all_words,
                timeline=job_tl,
                caption_style=resolve_style(cfg).caption_style,
            )
            log.info("剪映旁路导出 %s", jy)
        except Exception as exc:  # noqa: BLE001
            log.warning("剪映导出跳过: %s", exc)

    edit_json = cfg.paths.data / "jobs" / (room_id or media.stem) / "edit.json"
    if only is not None and edit_json.is_file():
        existing = read_json(edit_json)
        old_clips = [Path(p) for p in existing.get("clips") or []]
        new_names = {p.name for p in outputs}
        merged = [p for p in old_clips if p.name not in new_names] + outputs
        merged.sort(key=lambda p: p.name)
        write_json(
            edit_json,
            {
                "clips": [str(p) for p in merged],
                "media": str(media),
                "timeline": str(timeline_path(cfg, job_key)),
                "titles": existing.get("titles") or [h.title for h in highlights],
            },
        )
    else:
        write_json(
            edit_json,
            {
                "clips": [str(p) for p in outputs],
                "media": str(media),
                "timeline": str(timeline_path(cfg, job_key)),
                "titles": [h.title for h in highlights],
            },
        )
    return outputs


def compile_job(cfg: AppConfig, source: str | Path | None = None) -> Path:
    """xfade-concat top clips into output/clips/<room>_pack.mp4."""
    cfg.paths.ensure()
    job_key, clips = _load_rendered_clips(cfg, source)
    dest = cfg.paths.output / f"{job_key}_pack.mp4"
    xfade_concat(clips, dest, xfade=cfg.edit.xfade_seconds, transition=cfg.edit.xfade or "fadeblack")
    log.info("合集 %s (%s clips, xfade=%.2fs)", dest, len(clips), cfg.edit.xfade_seconds)
    return dest


def _load_rendered_clips(cfg: AppConfig, source: str | Path | None) -> tuple[str, list[Path]]:
    if source:
        path = Path(source)
        if path.is_file() and path.suffix.lower() == ".mp4":
            return path.stem, [path]
        if path.is_dir():
            clips = sorted(p for p in path.glob("*.mp4") if p.is_file() and "_pack" not in p.name)
            if clips:
                return path.name, clips
        edit_json = cfg.paths.data / "jobs" / str(source) / "edit.json"
        if edit_json.is_file():
            data = read_json(edit_json)
            clips = [Path(p) for p in data.get("clips") or [] if Path(p).is_file()]
            if clips:
                return str(source), clips
    job = latest_job(cfg)
    if job and (job / "edit.json").is_file():
        data = read_json(job / "edit.json")
        clips = [Path(p) for p in data.get("clips") or [] if Path(p).is_file()]
        if clips:
            return job.name, clips
    raise MediaError("没有可拼接的成片。先运行 dylive edit")


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
    highlights = [highlight_from_dict(row) for row in payload.get("highlights") or []]
    return media, highlights, path.parent.name


def render_clip(
    cfg: AppConfig,
    media: Path,
    highlight: Highlight,
    dest: Path,
    *,
    title: str,
    room_id: str | None,
    transcript: Transcript | None = None,
    cta: str | None = None,
    filler_cut: bool = False,
    effects: dict | None = None,
    caption_style: str | None = None,
) -> Path:
    if transcript is None or not transcript.words:
        raise MediaError("没有词级字幕，请先运行 dylive transcribe")
    ffmpeg = require_ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, highlight.start)
    dur = max(0.2, highlight.end - highlight.start)
    font = require_cjk_font()
    spec0 = resolve_style(cfg)
    if caption_style:
        spec0.caption_style = caption_style
    if effects:
        for _k, _v in effects.items():
            if isinstance(_v, bool) and hasattr(spec0, _k):
                setattr(spec0, _k, _v)
    clip_tl = build_clip_timeline(cfg, media, highlight, transcript, room_id=room_id, title=title)
    extra_effects = []
    vt = clip_tl.track("video")
    if vt and vt.clips:
        extra_effects = [
            e
            for e in (vt.clips[0].effects or [])
            if e.get("name") not in {"caption_mask", "progress_bar"}
        ]

    caption = ""
    if cfg.edit.source_caption and room_id:
        caption = f"来源 live.douyin.com/{room_id}"
    elif cfg.edit.source_caption:
        caption = "来源 抖音直播"

    words = slice_words(transcript.words, start, highlight.end, origin=start)
    if filler_cut:
        from dylive.create import filter_filler_words

        words = filter_filler_words(words)
    if not words:
        words = [Word(0.0, min(dur, 1.6), title[:12] or "高能", 1.0)]

    punch_abs = None
    if spec0.punch:
        punch_abs = loudest_interval(media, start, highlight.end, length=cfg.edit.punch_seconds)

    bgm_path = Path(cfg.edit.bgm) if cfg.edit.bgm else None
    if bgm_path and not bgm_path.is_file():
        log.info("BGM 文件不存在，跳过混音: %s", bgm_path)
        bgm_path = None

    last_err = ""
    attempts: list[tuple[object, bool]] = [(s, bool(bgm_path)) for s in fallback_specs(spec0)]
    if bgm_path:
        attempts.append((fallback_specs(spec0)[-1], False))
    for spec, use_bgm in attempts:
        words_use = list(words)
        dur_use = dur
        plan = None
        if spec.jumpcut:
            gaps = silence_gaps(words, dur, min_silence=0.4)
            plan = jumpcut_plan(dur, gaps, speed=spec.silence_speed)
            if plan_is_identity(plan):
                plan = None
            else:
                words_use = remap_words(words, plan)
                dur_use = warped_duration(dur, plan)

        ass_path = dest.with_suffix(".ass")
        write_ass(
            ass_path,
            words_use,
            style=spec.caption_style,
            width=cfg.edit.width,
            height=cfg.edit.height,
            font_path=font,
        )
        ass_filter = ffmpeg_subtitles_filter(ass_path, font)

        hook = first_sentence(words_use) if spec.hook else None
        if spec.hook and not hook:
            hook = (highlight.hook or title)[:16] if (highlight.hook or title) else None
        pops = keyword_pops(words_use, cfg.detect.keywords) if spec.keyword_pop else []
        punch = punch_abs if spec.punch else None

        graph, vlabel, alabel = build_filter_complex(
            spec,
            width=cfg.edit.width,
            height=cfg.edit.height,
            duration=dur_use,
            loudness_i=cfg.edit.loudness_i,
            ass_filter=ass_filter,
            font=font,
            source_caption=caption,
            hook_text=hook,
            hook_seconds=cfg.edit.hook_seconds,
            punch=punch,
            pops=pops,
            plan=plan,
            extra_effects=extra_effects if spec == spec0 else None,
            cta_text=cta,
            bgm=use_bgm,
        )
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
        if use_bgm and bgm_path:
            args += ["-stream_loop", "-1", "-i", str(bgm_path)]
        args += [
            "-filter_complex",
            graph,
            "-map",
            f"[{vlabel}]",
            "-map",
            f"[{alabel}]",
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
            str(dest),
        ]
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=max(120, dur * 10), check=False
        )
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            if spec != spec0 or (bgm_path and not use_bgm):
                log.warning("特效图降级 spec=%s bgm=%s", _spec_tag(spec), use_bgm)
            log.info("烧录字幕 %s  style=%s captions=%s", ass_path.name, spec.name, spec.caption_style)
            return dest
        last_err = (proc.stderr or proc.stdout or "")[-1500:]
        log.warning("特效图失败 (%s bgm=%s): %s", _spec_tag(spec), use_bgm, last_err[-400:])
        dest.unlink(missing_ok=True)

    raise MediaError(f"剪辑失败: {last_err}")


def _spec_tag(spec) -> str:
    flags = []
    if spec.punch:
        flags.append("punch")
    if spec.shake:
        flags.append("shake")
    if spec.jumpcut:
        flags.append("jumpcut")
    if spec.hook:
        flags.append("hook")
    if spec.progress:
        flags.append("bar")
    if spec.caption_mask:
        flags.append("mask")
    return spec.name + ("+" + ",".join(flags) if flags else "")
