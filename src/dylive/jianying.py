"""剪映 sidecar + pyJianYingDraft 草稿导出.

剪映 / CapCut **没有官方开放 API**。本模块用 pip 包 ``pyJianYingDraft``
写出「剪映专业版」桌面端能打开的草稿目录（``draft_content.json`` 等），
不逆向或绕过剪映 7+ 的草稿加密，也不用 Windows uiautomation 去点界面
（版本锁死、极脆；剪映 7+ 自动导出本身就不支持）。

Sidecar（成片 + srt + IMPORT.md）始终可用；真正的草稿需要::

    pip install 'dylive[jianying]'
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from dylive.captions import write_ass, write_srt
from dylive.config import AppConfig
from dylive.exceptions import DependencyError, MediaError
from dylive.media import duration_seconds
from dylive.state import latest_job, read_json
from dylive.timeline import Timeline, caption_words
from dylive.transcribe import Word

log = logging.getLogger("dylive.jianying")

IMPORT_MD = """# 导入剪映

这个目录同时包含：

1. **成片旁路**（`clip_*.mp4` + `captions.srt`）— 拖进剪映即可。
2. **pyJianYingDraft 草稿**（`draft/`）— 用「剪映专业版」打开该草稿目录。

## 草稿怎么打开

用「剪映专业版」打开 `draft/` 目录：设置里把草稿位置指过来，或把文件夹拷进剪映草稿目录。
Windows 可直接用；macOS/Linux 生成草稿后仍需在剪映里打开。
剪映 7+ 可能加密**旧**草稿，我们只**写新草稿**，不去解密。

剪映没有官方 API。本项目用 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) 写草稿。
不要用 uiautomation 自动点导出（剪映 7+ 不支持自动导出）。

字幕字体建议：Noto Sans CJK / 思源黑体。Debian：`sudo apt install fonts-noto-cjk`。
"""

OPEN_HINT = (
    "用「剪映专业版」打开该草稿目录（设置里把草稿位置指过来，或把文件夹拷进剪映草稿目录）。"
    "Windows 可直接用；macOS/Linux 生成草稿后仍需在剪映里打开。"
    "剪映 7+ 可能加密旧草稿，我们只写新草稿。"
)

# ffmpeg 预览成片 ↔ 剪映草稿滤镜/转场（名字能对上的就对上）
JIANYING_MAP: list[dict[str, str]] = [
    {"ffmpeg": "fade", "jianying": "叠化", "kind": "transition"},
    {"ffmpeg": "fadeblack", "jianying": "闪黑", "kind": "transition"},
    {"ffmpeg": "wipeleft", "jianying": "向左擦除", "kind": "transition"},
    {"ffmpeg": "slideleft", "jianying": "左移", "kind": "transition"},
    {"ffmpeg": "circlecrop", "jianying": "圆形遮罩", "kind": "transition"},
    {"ffmpeg": "punch_zoom", "jianying": "斜切", "kind": "intro"},
    {"ffmpeg": "shake", "jianying": "抖动", "kind": "transition"},
    {"ffmpeg": "flash", "jianying": "闪白", "kind": "transition"},
    {"ffmpeg": "glitch", "jianying": "故障", "kind": "transition"},
    {"ffmpeg": "rgb_split", "jianying": "色差故障", "kind": "transition"},
    {"ffmpeg": "mirror", "jianying": "镜像翻转", "kind": "transition"},
    {"ffmpeg": "grain", "jianying": "胶片", "kind": "filter"},
    {"ffmpeg": "vignette", "jianying": "暗角", "kind": "filter"},
    {"ffmpeg": "contrast", "jianying": "质感", "kind": "filter"},
    {"ffmpeg": "saturation", "jianying": "原生肤", "kind": "filter"},
    {"ffmpeg": "caption_mask", "jianying": "（成片烧录，草稿用字幕轨）", "kind": "note"},
    {"ffmpeg": "progress_bar", "jianying": "（成片烧录）", "kind": "note"},
]


def missing_library_error() -> DependencyError:
    return DependencyError(
        "缺少 pyJianYingDraft。请安装: pip install 'dylive[jianying]'\n"
        "剪映没有官方开放 API；本项目用 pyJianYingDraft 写专业版可打开的新草稿，"
        "不会去解密剪映 7+ 的旧草稿，也不会用 Windows uiautomation 自动点导出。"
    )


def jianying_available() -> bool:
    try:
        import pyJianYingDraft  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class DraftClip:
    """One timeline segment our wrapper will hand to pyJianYingDraft (or a fake writer)."""

    media: Path
    src_in: float = 0.0
    src_out: float = 0.0
    srt: Path | None = None
    title: str = ""
    effects: list[str] = field(default_factory=list)
    transition: str = "fadeblack"
    filter_name: str = "原生肤"
    intro_name: str = "斜切"

    @property
    def duration(self) -> float:
        return max(0.05, self.src_out - self.src_in)

    def to_dict(self) -> dict[str, Any]:
        return {
            "media": str(self.media),
            "in": round(self.src_in, 3),
            "out": round(self.src_out, 3),
            "srt": str(self.srt) if self.srt else None,
            "title": self.title,
            "effects": list(self.effects),
            "transition": self.transition,
            "filter": self.filter_name,
            "intro": self.intro_name,
        }


class DraftWriter(Protocol):
    def write(self, dest: Path, clips: list[DraftClip], **kwargs: Any) -> Path: ...


def export_jianying(
    cfg: AppConfig,
    room_id: str,
    clips: list[Path],
    *,
    words: list[Word],
    timeline: Timeline | None,
    caption_style: str = "douyin",
) -> Path:
    dest = cfg.paths.output.parent / "jianying" / room_id
    dest.mkdir(parents=True, exist_ok=True)
    for i, clip in enumerate(clips, start=1):
        if clip.is_file():
            shutil.copy2(clip, dest / f"clip_{i:02d}{clip.suffix}")
        ass = clip.with_suffix(".ass")
        if ass.is_file():
            shutil.copy2(ass, dest / f"clip_{i:02d}.ass")
    if words:
        write_srt(dest / "captions.srt", words)
        write_ass(
            dest / "captions.ass",
            words,
            style=caption_style,
            width=cfg.edit.width,
            height=cfg.edit.height,
        )
    elif timeline:
        tw = caption_words(timeline)
        if tw:
            write_srt(dest / "captions.srt", tw)
            write_ass(
                dest / "captions.ass",
                tw,
                style=caption_style,
                width=cfg.edit.width,
                height=cfg.edit.height,
            )
    if timeline:
        (dest / "timeline.json").write_text(
            __import__("json").dumps(timeline.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (dest / "IMPORT.md").write_text(IMPORT_MD, encoding="utf-8")
    return dest


def jianying_root(cfg: AppConfig, room_id: str) -> Path:
    return cfg.paths.output.parent / "jianying" / room_id


def draft_dir(cfg: AppConfig, room_id: str) -> Path:
    return jianying_root(cfg, room_id) / "draft"


def _filter_for_style(style: str) -> str:
    if style == "party":
        return "胶片"
    if style == "clean":
        return "原生肤"
    return "质感"


def _intro_for_style(style: str) -> str:
    if style == "party":
        return "斜切"
    if style == "clean":
        return "缩小"
    return "斜切"


def build_draft_segments(
    cfg: AppConfig,
    room: str | None = None,
) -> list[DraftClip]:
    """Build the clip list the writer will turn into a 剪映 draft. No third-party import."""
    job = _job_path(cfg, room)
    room_id = job.name
    style = cfg.edit.style or "douyin_hot"
    xfade = cfg.edit.xfade or "fadeblack"
    filt = _filter_for_style(style)
    intro = _intro_for_style(style)
    clips: list[DraftClip] = []

    rendered = _rendered_clips(cfg, job)
    srt = jianying_root(cfg, room_id) / "captions.srt"
    if not srt.is_file():
        per = None
        if rendered:
            cand = rendered[0].with_suffix(".srt")
            per = cand if cand.is_file() else None
        srt_path = srt if srt.is_file() else per
    else:
        srt_path = srt

    if rendered:
        for i, path in enumerate(rendered, start=1):
            dur = duration_seconds(path) if path.is_file() else 0.0
            clip_srt = path.with_suffix(".srt")
            clips.append(
                DraftClip(
                    media=path,
                    src_in=0.0,
                    src_out=max(0.2, dur),
                    srt=clip_srt if clip_srt.is_file() else srt_path,
                    title=f"{room_id} 切片 {i:02d}",
                    effects=_effects_from_job(job),
                    transition=xfade,
                    filter_name=filt,
                    intro_name=intro,
                )
            )
        return clips

    highs, media = _highlights_and_media(job)
    if media and highs:
        for i, h in enumerate(highs, start=1):
            clips.append(
                DraftClip(
                    media=media,
                    src_in=float(h.get("start") or 0),
                    src_out=float(h.get("out") or h.get("end") or 0),
                    srt=srt_path,
                    title=str(h.get("title") or f"{room_id} 高能 {i:02d}"),
                    effects=list((h.get("effects") or [])) or _effects_from_job(job),
                    transition=xfade,
                    filter_name=filt,
                    intro_name=intro,
                )
            )
    if not clips:
        raise MediaError(f"房间 {room_id} 没有可写入剪映草稿的成片或高能片段。先运行 dylive edit")
    return clips


def write_jianying_draft(
    cfg: AppConfig,
    room: str | None = None,
    *,
    writer: DraftWriter | None = None,
) -> Path:
    """Write a pyJianYingDraft folder under output/jianying/<room>/draft/."""
    job = _job_path(cfg, room)
    room_id = job.name
    clips = build_draft_segments(cfg, room_id)
    dest = draft_dir(cfg, room_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if writer is None:
        writer = PyJianYingDraftWriter()
    out = writer.write(
        dest,
        clips,
        width=cfg.edit.width,
        height=cfg.edit.height,
        room_id=room_id,
        xfade=cfg.edit.xfade or "fadeblack",
        style=cfg.edit.style or "douyin_hot",
    )
    log.info("剪映草稿 %s (%s clips)", out, len(clips))
    return Path(out)


class PyJianYingDraftWriter:
    """Talk to pyJianYingDraft. Isolated so tests can swap in a fake writer."""

    def write(self, dest: Path, clips: list[DraftClip], **kwargs: Any) -> Path:
        draft = _import_lib()
        width = int(kwargs.get("width") or 1080)
        height = int(kwargs.get("height") or 1920)
        room_id = str(kwargs.get("room_id") or dest.parent.name)
        xfade = str(kwargs.get("xfade") or "fadeblack")
        dest.parent.mkdir(parents=True, exist_ok=True)
        script = _create_script(draft, dest, width, height)
        _ensure_tracks(script, draft)
        assets = dest / "assets"
        assets.mkdir(parents=True, exist_ok=True)

        cursor = 0.0
        last_video = None
        combined_srt: Path | None = None
        for i, clip in enumerate(clips):
            media = _copy_media(clip.media, assets, i)
            dur = clip.duration
            seg = _make_video_segment(
                draft, media, cursor, dur, source_in=clip.src_in, source_out=clip.src_out
            )
            _apply_intro(draft, seg, clip.intro_name)
            _apply_filter(draft, seg, clip.filter_name)
            if last_video is not None:
                _apply_transition(draft, last_video, clip.transition or xfade)
            _add_segment(script, seg, "video")
            last_video = seg
            if clip.srt and clip.srt.is_file() and combined_srt is None:
                combined_srt = clip.srt
            cursor += dur

        if combined_srt and combined_srt.is_file():
            _import_srt(script, combined_srt)

        _save_script(script, dest)
        meta = dest / "DYLIVE.txt"
        meta.write_text(
            f"dylive 剪映草稿  room={room_id}\n{OPEN_HINT}\n",
            encoding="utf-8",
        )
        return dest


def _import_lib():
    try:
        import pyJianYingDraft as draft
    except ImportError as exc:
        raise missing_library_error() from exc
    return draft


def _create_script(draft, dest: Path, width: int, height: int):
    dest.mkdir(parents=True, exist_ok=True)
    DraftFolder = getattr(draft, "DraftFolder", None)
    if DraftFolder is not None:
        parent = dest.parent
        parent.mkdir(parents=True, exist_ok=True)
        folder = DraftFolder(str(parent))
        try:
            return folder.create_draft(dest.name, width, height, allow_replace=True)
        except TypeError:
            try:
                return folder.create_draft(dest.name, width, height)
            except FileExistsError:
                shutil.rmtree(dest, ignore_errors=True)
                return folder.create_draft(dest.name, width, height)
    Script = getattr(draft, "ScriptFile", None) or getattr(draft, "Script_file", None)
    if Script is None:
        raise DependencyError("pyJianYingDraft 没有 ScriptFile / Script_file")
    try:
        script = Script(width, height, 30, True)
    except TypeError:
        try:
            script = Script(width, height)
        except TypeError:
            script = Script(width, height, 30)
    script.save_path = str(dest / "draft_content.json")
    return script


def _ensure_tracks(script, draft) -> None:
    TrackType = getattr(draft, "TrackType", None)
    TrackSpec = getattr(draft, "TrackSpec", None)
    if TrackType is None:
        return
    if hasattr(script, "append_tracks") and TrackSpec is not None:
        try:
            script.append_tracks(
                [
                    TrackSpec(TrackType.video, "video"),
                    TrackSpec(TrackType.audio, "audio"),
                    TrackSpec(TrackType.text, "text"),
                ]
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("append_tracks failed: %s", exc)
    adder = getattr(script, "add_track", None)
    if adder:
        for kind, name in (("video", "video"), ("audio", "audio"), ("text", "text")):
            tt = getattr(TrackType, kind, None)
            if tt is None:
                continue
            try:
                adder(tt, name)
            except TypeError:
                try:
                    adder(tt, track_name=name)
                except Exception as exc:  # noqa: BLE001
                    log.debug("add_track %s: %s", name, exc)
            except Exception as exc:  # noqa: BLE001
                log.debug("add_track %s: %s", name, exc)


def _make_video_segment(draft, media: Path, start: float, dur: float, *, source_in: float, source_out: float):
    trange = getattr(draft, "trange", None)
    VideoSegment = getattr(draft, "VideoSegment", None) or getattr(draft, "Video_segment", None)
    if VideoSegment is None or trange is None:
        raise DependencyError("pyJianYingDraft 缺少 VideoSegment / trange")
    target = trange(f"{start:.3f}s", f"{dur:.3f}s")
    src_dur = max(0.05, source_out - source_in)
    try:
        return VideoSegment(
            str(media),
            target,
            source_timerange=trange(f"{source_in:.3f}s", f"{src_dur:.3f}s"),
        )
    except TypeError:
        return VideoSegment(str(media), target)


def _apply_intro(draft, seg, name: str) -> None:
    IntroType = getattr(draft, "IntroType", None)
    if IntroType is None or not hasattr(seg, "add_animation"):
        return
    member = _enum_member(IntroType, name, "斜切")
    if member is None:
        return
    try:
        seg.add_animation(member)
    except Exception as exc:  # noqa: BLE001
        log.debug("intro skipped: %s", exc)


def _apply_filter(draft, seg, name: str) -> None:
    FilterType = getattr(draft, "FilterType", None)
    if FilterType is None or not hasattr(seg, "add_filter"):
        return
    member = _enum_member(FilterType, name, "原生肤")
    if member is None:
        return
    try:
        seg.add_filter(member, 40)
    except TypeError:
        try:
            seg.add_filter(member)
        except Exception as exc:  # noqa: BLE001
            log.debug("filter skipped: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.debug("filter skipped: %s", exc)


def _apply_transition(draft, prev_seg, ffmpeg_name: str) -> None:
    TransitionType = getattr(draft, "TransitionType", None)
    if TransitionType is None or not hasattr(prev_seg, "add_transition"):
        return
    mapped = {
        "fade": "叠化",
        "fadeblack": "闪黑",
        "wipeleft": "向左擦除",
        "slideleft": "左移",
        "circlecrop": "圆形遮罩",
        "glitch": "故障",
    }.get(ffmpeg_name, ffmpeg_name)
    member = _enum_member(TransitionType, mapped, "叠化")
    if member is None:
        return
    try:
        prev_seg.add_transition(member)
    except Exception as exc:  # noqa: BLE001
        log.debug("transition skipped: %s", exc)


def _add_segment(script, seg, track: str) -> None:
    try:
        script.add_segment(seg, track)
        return
    except TypeError:
        pass
    try:
        script.add_segment(seg, track_name=track)
        return
    except TypeError:
        pass
    script.add_segment(seg)


def _import_srt(script, srt: Path) -> None:
    fn = getattr(script, "import_srt", None)
    if not fn:
        return
    try:
        fn(str(srt), track_name="text")
    except TypeError:
        try:
            fn(str(srt), "text")
        except Exception as exc:  # noqa: BLE001
            log.debug("import_srt skipped: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.debug("import_srt skipped: %s", exc)


def _save_script(script, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if hasattr(script, "save"):
        try:
            script.save()
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("script.save failed: %s", exc)
    dump = getattr(script, "dump", None)
    if dump:
        dump(str(dest / "draft_content.json"))
        return
    dumps = getattr(script, "dumps", None)
    if dumps:
        (dest / "draft_content.json").write_text(dumps(), encoding="utf-8")
        return
    raise DependencyError("无法把草稿写到磁盘（ScriptFile.save / dump 都不可用）")


def _enum_member(enum_cls, name: str, fallback: str | None = None):
    if name and hasattr(enum_cls, name):
        return getattr(enum_cls, name)
    from_name = getattr(enum_cls, "from_name", None)
    if from_name and name:
        try:
            return from_name(name)
        except Exception:  # noqa: BLE001
            pass
    if fallback and fallback != name:
        return _enum_member(enum_cls, fallback, None)
    # last resort: first public member
    for attr in dir(enum_cls):
        if attr.startswith("_"):
            continue
        val = getattr(enum_cls, attr, None)
        if val is not None and not callable(val):
            return val
    return None


def _copy_media(src: Path, assets: Path, index: int) -> Path:
    dest = assets / f"clip_{index:02d}{src.suffix or '.mp4'}"
    if src.is_file():
        try:
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            dest.symlink_to(src.resolve())
        except OSError:
            shutil.copy2(src, dest)
    return dest


def _job_path(cfg: AppConfig, room: str | None) -> Path:
    if room:
        path = cfg.paths.data / "jobs" / str(room)
        if path.is_dir():
            return path
        raise MediaError(f"找不到 job: {room}")
    job = latest_job(cfg)
    if job is None:
        raise MediaError("没有 job。先运行 dylive run / edit")
    return job


def _rendered_clips(cfg: AppConfig, job: Path) -> list[Path]:
    edit_json = job / "edit.json"
    if edit_json.is_file():
        data = read_json(edit_json)
        clips = [Path(p) for p in data.get("clips") or [] if Path(p).is_file()]
        if clips:
            return clips
    out = cfg.paths.output
    if out.is_dir():
        found = sorted(
            p for p in out.glob(f"{job.name}_*.mp4") if p.is_file() and "_pack" not in p.name
        )
        if found:
            return found
    side = jianying_root(cfg, job.name)
    if side.is_dir():
        found = sorted(p for p in side.glob("clip_*.mp4") if p.is_file())
        if found:
            return found
    return []


def _highlights_and_media(job: Path) -> tuple[list[dict], Path | None]:
    highs_path = job / "highlights.json"
    if not highs_path.is_file():
        return [], None
    data = read_json(highs_path)
    media = Path(data["media"]) if data.get("media") else None
    return list(data.get("highlights") or []), media


def _effects_from_job(job: Path) -> list[str]:
    tl = job / "timeline.json"
    if not tl.is_file():
        return []
    data = read_json(tl)
    names: list[str] = []
    for track in data.get("tracks") or []:
        if track.get("type") != "video":
            continue
        for clip in track.get("clips") or []:
            for e in clip.get("effects") or []:
                n = e.get("name") or e.get("type")
                if n and n not in names:
                    names.append(n)
    return names
