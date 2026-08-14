"""CcClip-style multi-track timeline.

Clips never rewrite the original recording: they only store src + in/out.
`edit` compiles a clip timeline to ffmpeg; `compile` xfades rendered outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dylive.captions import first_sentence, slice_words
from dylive.config import AppConfig
from dylive.detect import Highlight
from dylive.effects import keyword_pops, loudest_interval, resolve_style
from dylive.state import write_json
from dylive.transcribe import Transcript, Word


@dataclass
class TlClip:
    src: str | None = None
    src_in: float | None = None
    src_out: float | None = None
    start: float | None = None
    end: float | None = None
    text: str | None = None
    style: str | None = None
    name: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    effects: list[dict[str, Any]] = field(default_factory=list)
    duck: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.src is not None:
            d["src"] = self.src
        if self.src_in is not None:
            d["in"] = round(self.src_in, 3)
        if self.src_out is not None:
            d["out"] = round(self.src_out, 3)
        if self.start is not None:
            d["start"] = round(self.start, 3)
        if self.end is not None:
            d["end"] = round(self.end, 3)
        if self.text is not None:
            d["text"] = self.text
        if self.style is not None:
            d["style"] = self.style
        if self.name is not None:
            d["name"] = self.name
        if self.params:
            d["params"] = self.params
        if self.effects:
            d["effects"] = self.effects
        if self.duck:
            d["duck"] = True
        return d


@dataclass
class Track:
    type: str
    clips: list[TlClip] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "clips": [c.to_dict() for c in self.clips]}


@dataclass
class Timeline:
    media: str
    width: int
    height: int
    duration: float = 0.0
    tracks: list[Track] = field(default_factory=list)
    style: str = "douyin_hot"

    def to_dict(self) -> dict[str, Any]:
        return {
            "media": self.media,
            "width": self.width,
            "height": self.height,
            "duration": round(self.duration, 3),
            "style": self.style,
            "tracks": [t.to_dict() for t in self.tracks],
        }

    def track(self, kind: str) -> Track | None:
        for t in self.tracks:
            if t.type == kind:
                return t
        return None


def timeline_path(cfg: AppConfig, job_key: str) -> Path:
    return cfg.paths.data / "jobs" / job_key / "timeline.json"


def save_timeline(path: Path, timeline: Timeline) -> Path:
    write_json(path, timeline.to_dict())
    return path


def build_clip_timeline(
    cfg: AppConfig,
    media: Path,
    highlight: Highlight,
    transcript: Transcript,
    *,
    room_id: str | None = None,
    title: str = "",
) -> Timeline:
    """One highlight → multi-track timeline pointing at the original file."""
    spec = resolve_style(cfg)
    src_in = max(0.0, highlight.start)
    src_out = max(src_in + 0.2, highlight.end)
    dur = src_out - src_in
    words = slice_words(transcript.words, src_in, src_out, origin=src_in)
    punch = None
    if spec.punch:
        punch = loudest_interval(media, src_in, src_out, length=cfg.edit.punch_seconds)

    effects: list[dict[str, Any]] = []
    if spec.saturation:
        effects.append({"name": "saturation", "params": {"amount": 1.14}})
    if spec.fade_in:
        effects.append({"name": "fade", "params": {"type": "in", "duration": 0.25}})
    if spec.punch and punch:
        effects.append({"name": "punch_zoom", "params": {"start": round(punch[0], 3), "end": round(punch[1], 3)}})
    if spec.caption_mask:
        effects.append({"name": "caption_mask", "params": {"ratio": 0.16}})
    if spec.progress:
        effects.append({"name": "progress_bar", "params": {}})

    video = Track(
        type="video",
        clips=[
            TlClip(
                src=str(media),
                src_in=src_in,
                src_out=src_out,
                effects=effects,
            )
        ],
    )
    captions = Track(
        type="caption",
        clips=[
            TlClip(start=w.start, end=w.end, text=w.word, style=spec.caption_style)
            for w in words
            if (w.word or "").strip()
        ],
    )
    fx_clips: list[TlClip] = []
    if spec.punch and punch:
        fx_clips.append(
            TlClip(start=punch[0], end=punch[1], name="punch_zoom", params={"amount": 1.16})
        )
    if spec.fade_in:
        fx_clips.append(TlClip(start=0.0, end=0.25, name="fade", params={"type": "in"}))
    if spec.caption_mask:
        fx_clips.append(TlClip(start=0.0, end=dur, name="caption_mask", params={"ratio": 0.16}))
    effect_track = Track(type="effect", clips=fx_clips)

    overlays: list[TlClip] = []
    hook = first_sentence(words) or (title[:16] if title else "")
    if spec.hook and hook:
        overlays.append(TlClip(start=0.0, end=min(dur, cfg.edit.hook_seconds), text=hook))
    if cfg.edit.source_caption:
        src_text = f"来源 live.douyin.com/{room_id}" if room_id else "来源 抖音直播"
        overlays.append(TlClip(start=0.0, end=dur, text=src_text))
    for ps, pe, tok in keyword_pops(words, cfg.detect.keywords) if spec.keyword_pop else []:
        overlays.append(TlClip(start=ps, end=pe, text=tok))
    overlay_track = Track(type="overlay", clips=overlays)

    audio_clips: list[TlClip] = [TlClip(src=str(media), src_in=src_in, src_out=src_out)]
    bgm = cfg.edit.bgm
    if bgm and Path(bgm).is_file():
        audio_clips.append(TlClip(src=str(Path(bgm)), src_in=0.0, src_out=dur, duck=True))
    audio_track = Track(type="audio", clips=audio_clips)

    return Timeline(
        media=str(media),
        width=cfg.edit.width,
        height=cfg.edit.height,
        duration=dur,
        style=spec.name,
        tracks=[video, captions, effect_track, overlay_track, audio_track],
    )


def build_job_timeline(
    cfg: AppConfig,
    media: Path,
    highlights: list[Highlight],
    transcript: Transcript,
    *,
    room_id: str | None = None,
) -> Timeline:
    """All highlights as sequential video clips still pointing at the original src in/out."""
    spec = resolve_style(cfg)
    video_clips: list[TlClip] = []
    caption_clips: list[TlClip] = []
    effect_clips: list[TlClip] = []
    overlay_clips: list[TlClip] = []
    cursor = 0.0
    for h in highlights:
        clip_tl = build_clip_timeline(cfg, media, h, transcript, room_id=room_id, title=h.title or "")
        dur = clip_tl.duration
        for c in clip_tl.track("video").clips if clip_tl.track("video") else []:
            video_clips.append(c)
        for c in clip_tl.track("caption").clips if clip_tl.track("caption") else []:
            caption_clips.append(
                TlClip(
                    start=(c.start or 0) + cursor,
                    end=(c.end or 0) + cursor,
                    text=c.text,
                    style=c.style,
                )
            )
        for c in clip_tl.track("effect").clips if clip_tl.track("effect") else []:
            effect_clips.append(
                TlClip(
                    start=(c.start or 0) + cursor,
                    end=(c.end or 0) + cursor,
                    name=c.name,
                    params=c.params,
                )
            )
        for c in clip_tl.track("overlay").clips if clip_tl.track("overlay") else []:
            overlay_clips.append(
                TlClip(
                    start=(c.start or 0) + cursor,
                    end=(c.end or 0) + cursor,
                    text=c.text,
                )
            )
        cursor += dur
    audio = [TlClip(src=str(media), src_in=highlights[0].start if highlights else 0, src_out=highlights[-1].end if highlights else 0)]
    bgm = cfg.edit.bgm
    if bgm and Path(bgm).is_file():
        audio.append(TlClip(src=str(Path(bgm)), src_in=0.0, src_out=cursor, duck=True))
    return Timeline(
        media=str(media),
        width=cfg.edit.width,
        height=cfg.edit.height,
        duration=cursor,
        style=spec.name,
        tracks=[
            Track("video", video_clips),
            Track("caption", caption_clips),
            Track("effect", effect_clips),
            Track("overlay", overlay_clips),
            Track("audio", audio),
        ],
    )


def caption_words(timeline: Timeline) -> list[Word]:
    track = timeline.track("caption")
    if not track:
        return []
    return [
        Word(start=c.start or 0.0, end=c.end or 0.04, word=c.text or "", prob=1.0)
        for c in track.clips
        if c.text
    ]
