"""ffmpeg effect library: easing zoom/pan, punch, shake, flash, fade, mask, xfade, BGM.

Other agents can call render_effect(), build_filter_complex(), xfade_concat().
Inspired by common 抖音风 templates (zoom/pan/easing, punch, caption bar) — implemented
with ffmpeg, not MoviePy, and not vendored from other repos.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import shutil
import subprocess

import numpy as np

from dylive.config import AppConfig
from dylive.exceptions import MediaError
from dylive.media import duration_seconds, extract_pcm_s16le, require_ffmpeg
from dylive.transcribe import Word

EFFECT_NAMES = (
    "zoom_in",
    "zoom_out",
    "pan",
    "punch_zoom",
    "shake",
    "flash",
    "fade",
    "caption_mask",
    "progress_bar",
    "saturation",
    "vignette",
    "grain",
    "glitch",
    "rgb_split",
    "contrast",
    "freeze",
    "speed_ramp",
    "mirror",
)

XFADE_TYPES = ("fade", "fadeblack", "wipeleft", "slideleft", "circlecrop", "slideup")

EFFECT_LABELS = {
    "zoom_in": "缓入放大",
    "zoom_out": "缓出缩小",
    "pan": "平移",
    "punch_zoom": "能量峰冲击放大",
    "shake": "抖动",
    "flash": "闪白",
    "fade": "淡入淡出",
    "caption_mask": "口播底栏遮罩",
    "progress_bar": "底部进度条",
    "saturation": "饱和度",
    "vignette": "暗角",
    "grain": "胶片颗粒",
    "glitch": "故障闪切",
    "rgb_split": "RGB 色差分裂",
    "contrast": "质感对比",
    "freeze": "定格",
    "speed_ramp": "局部变速",
    "mirror": "镜像一击",
    "fadeblack": "闪黑转场",
    "wipeleft": "左擦除转场",
    "slideleft": "左滑转场",
    "circlecrop": "圆形遮罩转场",
    "slideup": "上滑转场",
}


@dataclass
class StyleSpec:
    name: str
    fill: str
    caption_style: str
    punch: bool
    shake: bool
    jumpcut: bool
    hook: bool
    saturation: bool
    progress: bool
    keyword_pop: bool
    caption_mask: bool = True
    fade_in: bool = True
    silence_speed: float = 1.12
    vignette: bool = False
    grain: bool = False
    glitch: bool = False
    contrast: bool = False
    freeze: bool = False
    speed_ramp: bool = False
    mirror: bool = False


def resolve_style(cfg: AppConfig) -> StyleSpec:
    name = (cfg.edit.style or "douyin_hot").strip()
    caption = cfg.edit.caption_style or "douyin"
    mask = cfg.edit.caption_mask
    fade = cfg.edit.fade_in
    if name == "clean":
        spec = StyleSpec(
            name="clean",
            fill="crop",
            caption_style="standard",
            punch=False,
            shake=False,
            jumpcut=False,
            hook=False,
            saturation=False,
            progress=False,
            keyword_pop=False,
            caption_mask=False,
            fade_in=False,
            vignette=False,
            grain=False,
            glitch=False,
            contrast=False,
        )
    elif name == "party":
        spec = StyleSpec(
            name="party",
            fill="blur",
            caption_style=caption,
            punch=True,
            shake=True,
            jumpcut=True,
            hook=True,
            saturation=True,
            progress=True,
            keyword_pop=True,
            caption_mask=mask,
            fade_in=fade,
            silence_speed=cfg.edit.silence_speed,
            vignette=True,
            grain=True,
            glitch=True,
            contrast=True,
        )
    elif name == "cinematic":
        spec = StyleSpec(
            name="cinematic",
            fill="crop",
            caption_style="standard",
            punch=False,
            shake=False,
            jumpcut=False,
            hook=False,
            saturation=True,
            progress=False,
            keyword_pop=False,
            caption_mask=False,
            fade_in=True,
            vignette=True,
            grain=True,
            glitch=False,
            contrast=True,
        )
    elif name == "vlog":
        spec = StyleSpec(
            name="vlog",
            fill="blur",
            caption_style=caption,
            punch=True,
            shake=False,
            jumpcut=False,
            hook=True,
            saturation=True,
            progress=True,
            keyword_pop=False,
            caption_mask=mask,
            fade_in=fade,
            vignette=False,
            grain=True,
            glitch=False,
            contrast=True,
        )
    else:
        spec = StyleSpec(
            name="douyin_hot",
            fill="blur",
            caption_style=caption,
            punch=True,
            shake=False,
            jumpcut=False,
            hook=True,
            saturation=True,
            progress=True,
            keyword_pop=False,
            caption_mask=mask,
            fade_in=fade,
            vignette=True,
            grain=True,
            glitch=False,
            contrast=True,
        )
    if cfg.edit.zoom_punch is not None:
        spec.punch = cfg.edit.zoom_punch
    if cfg.edit.shake is not None:
        spec.shake = cfg.edit.shake
    if cfg.edit.jumpcut is not None:
        spec.jumpcut = cfg.edit.jumpcut
    if cfg.edit.keyword_pop is not None:
        spec.keyword_pop = cfg.edit.keyword_pop
    if cfg.edit.vignette is not None:
        spec.vignette = cfg.edit.vignette
    if cfg.edit.grain is not None:
        spec.grain = cfg.edit.grain
    if cfg.edit.glitch is not None:
        spec.glitch = cfg.edit.glitch
    if getattr(cfg.edit, "progress", None) is not None:
        spec.progress = cfg.edit.progress
    return spec


def fallback_specs(spec: StyleSpec) -> list[StyleSpec]:
    out = [spec]
    stripped = replace(
        spec,
        shake=False,
        jumpcut=False,
        keyword_pop=False,
        glitch=False,
        freeze=False,
        speed_ramp=False,
        mirror=False,
    )
    if stripped != spec:
        out.append(stripped)
    mild = replace(
        stripped,
        grain=False,
        vignette=False,
        contrast=stripped.contrast,
    )
    if mild not in out and mild != stripped:
        out.append(mild)
    core = replace(
        spec,
        shake=False,
        jumpcut=False,
        keyword_pop=False,
        punch=False,
        progress=False,
        hook=False,
        fade_in=False,
        grain=False,
        vignette=False,
        glitch=False,
        freeze=False,
        speed_ramp=False,
        mirror=False,
        contrast=False,
        saturation=spec.saturation if spec.name != "clean" else False,
        caption_mask=spec.caption_mask if spec.name != "clean" else False,
    )
    if core not in out:
        out.append(core)
    return out


def ease_in_out_expr(duration: float, t: str = "t") -> str:
    """Smoothstep 0→1. Commas escaped for ffmpeg filtergraph."""
    dur = max(0.05, float(duration))
    p = f"min(1\\,max(0\\,{t}/{dur:.4f}))"
    return f"({p})*({p})*(3-2*({p}))"


def render_effect(
    name: str,
    vin: str,
    vout: str,
    params: dict | None = None,
    *,
    width: int,
    height: int,
    duration: float,
) -> str | None:
    """Return one filtergraph chain `[vin]...[vout]`, or None if unknown."""
    params = dict(params or {})
    key = (name or "").lower().replace("-", "_")
    if key in {"zoom_in", "zoomin"}:
        return _zoom(vin, vout, width, height, duration, amount=float(params.get("amount") or 0.14), reverse=False)
    if key in {"zoom_out", "zoomout"}:
        return _zoom(vin, vout, width, height, duration, amount=float(params.get("amount") or 0.14), reverse=True)
    if key == "pan":
        return _pan(
            vin, vout, width, height, duration,
            direction=str(params.get("direction") or "left"),
            amount=float(params.get("amount") or 0.12),
        )
    if key in {"punch_zoom", "punch", "zoom_punch"}:
        start = float(params.get("start") or 0.0)
        end = float(params.get("end") or (start + 0.45))
        return _punch_chain(vin, vout, width, height, start, end)
    if key == "shake":
        start = float(params.get("start") or 0.0)
        end = float(params.get("end") or (start + 0.4))
        return _shake_chain(vin, vout, width, height, start, end)
    if key == "flash":
        start = float(params.get("start") or 0.0)
        end = float(params.get("end") or (start + 0.12))
        return (
            f"[{vin}]eq=brightness='if(between(t,{start:.3f},{end:.3f})\\,"
            f"0.5*sin(PI*(t-{start:.3f})/{max(end - start, 0.05):.3f})\\,0)'[{vout}]"
        )
    if key == "fade":
        kind = str(params.get("type") or params.get("kind") or "in")
        fade_d = float(params.get("duration") or 0.25)
        if kind == "out":
            st = max(0.0, duration - fade_d)
            return f"[{vin}]fade=t=out:st={st:.3f}:d={fade_d:.3f}[{vout}]"
        return f"[{vin}]fade=t=in:st=0:d={fade_d:.3f}[{vout}]"
    if key in {"caption_mask", "subtitle_box", "mask"}:
        ratio = float(params.get("ratio") or 0.16)
        return (
            f"[{vin}]drawbox=x=0:y=ih*(1-{ratio:.3f}):w=iw:h=ih*{ratio:.3f}:"
            f"color=black@0.55:t=fill[{vout}]"
        )
    if key in {"progress_bar", "progress"}:
        hbar = max(6, height // 160)
        dur = max(0.05, duration)
        return (
            f"[{vin}]drawbox=x=0:y=ih-{hbar}:w='iw*t/{dur:.3f}':h={hbar}:"
            f"color=0xFF2D55@0.92:t=fill[{vout}]"
        )
    if key in {"saturation", "sat"}:
        sat = float(params.get("amount") or 1.14)
        return f"[{vin}]eq=saturation={sat:.3f}:contrast=1.05[{vout}]"
    if key in {"vignette", "dark_corners"}:
        angle = str(params.get("angle") or "PI/4")
        return f"[{vin}]vignette={angle}[{vout}]"
    if key in {"grain", "noise", "film_grain"}:
        amount = float(params.get("amount") or 10)
        return f"[{vin}]noise=alls={amount:.1f}:allf=t+u[{vout}]"
    if key in {"glitch", "glitch_pulse"}:
        start = float(params.get("start") or 0.0)
        end = float(params.get("end") or (start + 0.35))
        return (
            f"[{vin}]chromashift=cbh=10:crh=-10:enable='between(t,{start:.3f},{end:.3f})'[{vout}]"
        )
    if key in {"rgb_split", "rgb-split", "rgbsplit", "chromashift"}:
        start = params.get("start")
        end = params.get("end")
        shift = int(params.get("amount") or 6)
        rgb = f"rgbashift=rh={shift}:bh=-{shift}:rv=2:bv=-2"
        if start is not None and end is not None:
            rgb += f":enable='between(t,{float(start):.3f},{float(end):.3f})'"
        return f"[{vin}]format=rgba,{rgb},format=yuv420p[{vout}]"
    if key in {"contrast", "eq", "zhigan", "质感"}:
        amount = float(params.get("amount") or 1.12)
        sat = float(params.get("saturation") or 1.08)
        gamma = float(params.get("gamma") or 0.98)
        return f"[{vin}]eq=contrast={amount:.3f}:saturation={sat:.3f}:gamma={gamma:.3f}[{vout}]"
    if key == "freeze":
        start = float(params.get("start") or 0.5)
        hold = float(params.get("duration") or params.get("hold") or 0.24)
        return _freeze_chain(vin, vout, start, hold)
    if key in {"speed_ramp", "speedramp"}:
        start = float(params.get("start") or 0.4)
        end = float(params.get("end") or 1.2)
        speed = float(params.get("speed") or 1.25)
        duration = float(params.get("clip_duration") or duration)
        return _speed_ramp_chain(vin, vout, start, end, speed, duration)
    if key == "mirror":
        start = float(params.get("start") or 0.0)
        end = float(params.get("end") or (start + 0.2))
        return _mirror_chain(vin, vout, start, end)
    return None


def style_named_effects(
    spec: StyleSpec,
    *,
    punch: tuple[float, float] | None = None,
    duration: float = 2.0,
) -> list[dict]:
    """ffmpeg effects stacked by preset. caption_mask / progress are applied later (on top)."""
    named: list[dict] = []
    peak = punch or (max(0.0, duration * 0.35), min(duration, duration * 0.35 + 0.4))
    if spec.contrast:
        named.append({"name": "contrast", "params": {"amount": 1.12}})
    if spec.saturation:
        named.append({"name": "saturation", "params": {"amount": 1.14}})
    if spec.vignette:
        named.append({"name": "vignette", "params": {"angle": "PI/4"}})
    if spec.grain:
        named.append({"name": "grain", "params": {"amount": 14 if spec.name == "party" else 8}})
    if spec.fade_in:
        named.append({"name": "fade", "params": {"type": "in", "duration": 0.25}})
    if spec.punch and punch:
        named.append({"name": "punch_zoom", "params": {"start": punch[0], "end": punch[1]}})
    if spec.glitch:
        named.append({"name": "glitch", "params": {"start": peak[0], "end": peak[1]}})
        named.append({"name": "rgb_split", "params": {"start": peak[0], "end": peak[1], "amount": 6}})
    if spec.freeze and punch:
        named.append({"name": "freeze", "params": {"start": punch[0], "duration": 0.16}})
    if spec.mirror and punch:
        named.append({"name": "mirror", "params": {"start": punch[0], "end": min(punch[1], punch[0] + 0.2)}})
    if spec.speed_ramp:
        named.append(
            {
                "name": "speed_ramp",
                "params": {"start": duration * 0.25, "end": duration * 0.55, "speed": 1.2, "clip_duration": duration},
            }
        )
    return named


def effect_catalog() -> dict:
    ffmpeg = [{"name": n, "label": EFFECT_LABELS.get(n, n), "kind": "ffmpeg"} for n in EFFECT_NAMES]
    xfade = [{"name": n, "label": EFFECT_LABELS.get(n, n), "kind": "xfade"} for n in XFADE_TYPES]
    return {"ffmpeg": ffmpeg, "xfade": xfade, "presets": ["douyin_hot", "party", "clean"]}


def apply_named_effects(
    filters: list[str],
    vin: str,
    effects: list[dict],
    *,
    width: int,
    height: int,
    duration: float,
    prefix: str = "fx",
) -> str:
    v = vin
    n = 0
    for eff in effects or []:
        name = str(eff.get("name") or eff.get("type") or "")
        params = eff.get("params") if isinstance(eff.get("params"), dict) else {
            k: val for k, val in eff.items() if k not in {"name", "type", "params"}
        }
        vout = f"{prefix}{n}"
        snippet = render_effect(name, v, vout, params, width=width, height=height, duration=duration)
        if snippet:
            filters.append(snippet)
            v = vout
            n += 1
    return v


def _zoom(vin: str, vout: str, w: int, h: int, duration: float, *, amount: float, reverse: bool) -> str:
    # scale eval=frame: crop w/h is not per-frame on many ffmpeg builds.
    e = ease_in_out_expr(duration)
    z = f"(1+{amount:.3f}*(1-{e}))" if reverse else f"(1+{amount:.3f}*{e})"
    return (
        f"[{vin}]scale=w='iw*{z}':h='ih*{z}':eval=frame,crop={w}:{h}:(iw-ow)/2:(ih-oh)/2[{vout}]"
    )


def _pan(vin: str, vout: str, w: int, h: int, duration: float, *, direction: str, amount: float) -> str:
    e = ease_in_out_expr(duration)
    z = 1.0 + max(0.06, amount)
    d = direction.lower()
    if d == "right":
        x, y = f"(iw-ow)*{e}", "(ih-oh)/2"
    elif d == "up":
        x, y = "(iw-ow)/2", f"(ih-oh)*(1-{e})"
    elif d == "down":
        x, y = "(iw-ow)/2", f"(ih-oh)*{e}"
    else:
        x, y = f"(iw-ow)*(1-{e})", "(ih-oh)/2"
    return (
        f"[{vin}]scale={z:.3f}*iw:{z:.3f}*ih,crop={w}:{h}:'{x}':'{y}'[{vout}]"
    )


def _punch_chain(vin: str, vout: str, w: int, h: int, start: float, end: float) -> str:
    mid = f"{vin}_p"
    src = f"{vin}_ps"
    punch = f"{vin}_pz"
    return (
        f"[{vin}]split[{mid}][{src}];"
        f"[{src}]scale=iw*1.16:ih*1.16,crop={w}:{h}[{punch}];"
        f"[{mid}][{punch}]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[{vout}]"
    )


def _shake_chain(vin: str, vout: str, w: int, h: int, start: float, end: float) -> str:
    mid = f"{vin}_s"
    src = f"{vin}_ss"
    sh = f"{vin}_sh"
    return (
        f"[{vin}]split[{mid}][{src}];"
        f"[{src}]crop=iw-20:ih-20:'10+9*sin(18*t)':'10+7*cos(22*t)',scale={w}:{h}[{sh}];"
        f"[{mid}][{sh}]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[{vout}]"
    )


def _freeze_chain(vin: str, vout: str, start: float, hold: float) -> str:
    hold = max(0.08, hold)
    base = f"{vin}_fz"
    fr = f"{vin}_fr"
    held = f"{vin}_hold"
    return (
        f"[{vin}]split[{base}][{fr}];"
        f"[{fr}]trim=start={start:.3f}:end={start + 0.04:.3f},setpts=PTS-STARTPTS,"
        f"loop=loop=8:size=1:start=0,setpts=N/25/TB[{held}];"
        f"[{base}][{held}]overlay=0:0:enable='between(t,{start:.3f},{start + hold:.3f})'[{vout}]"
    )


def _speed_ramp_chain(
    vin: str, vout: str, start: float, end: float, speed: float, duration: float
) -> str:
    start = max(0.0, start)
    end = min(max(start + 0.05, end), max(start + 0.05, duration))
    speed = min(1.5, max(1.08, speed))
    pre, mid, post = f"{vin}_sp0", f"{vin}_sp1", f"{vin}_sp2"
    p0, p1, p2 = f"{vin}_p0", f"{vin}_p1", f"{vin}_p2"
    return (
        f"[{vin}]split=3[{pre}][{mid}][{post}];"
        f"[{pre}]trim=0:{start:.3f},setpts=PTS-STARTPTS[{p0}];"
        f"[{mid}]trim={start:.3f}:{end:.3f},setpts=PTS-STARTPTS,setpts=PTS/{speed:.3f}[{p1}];"
        f"[{post}]trim=start={end:.3f},setpts=PTS-STARTPTS[{p2}];"
        f"[{p0}][{p1}][{p2}]concat=n=3:v=1:a=0[{vout}]"
    )


def _mirror_chain(vin: str, vout: str, start: float, end: float) -> str:
    base = f"{vin}_mb"
    fl = f"{vin}_mf"
    mir = f"{vin}_mir"
    return (
        f"[{vin}]split[{base}][{fl}];"
        f"[{fl}]hflip[{mir}];"
        f"[{base}][{mir}]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[{vout}]"
    )


def loudest_interval(
    media: Path,
    start: float,
    end: float,
    *,
    length: float = 0.45,
    sample_rate: int = 8000,
) -> tuple[float, float]:
    length = min(max(0.3, length), 0.6)
    pcm = extract_pcm_s16le(media, sample_rate=sample_rate)
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    a = max(0, int(start * sample_rate))
    b = min(len(samples), int(end * sample_rate))
    clip = samples[a:b]
    dur = max(0.05, (b - a) / sample_rate)
    win = int(length * sample_rate)
    if len(clip) < win:
        return 0.0, min(length, dur)
    hop = max(1, int(0.01 * sample_rate))
    best_i, best = 0, -1.0
    for i in range(0, len(clip) - win + 1, hop):
        rms = float(np.sqrt(np.mean(clip[i : i + win] ** 2)))
        if rms > best:
            best, best_i = rms, i
    ps = best_i / sample_rate
    return ps, min(dur, ps + length)


def silence_gaps(words: list[Word], duration: float, *, min_silence: float = 0.4) -> list[tuple[float, float]]:
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for w in sorted(words, key=lambda x: x.start):
        if w.start - cursor >= min_silence:
            gaps.append((cursor, w.start))
        cursor = max(cursor, w.end)
    if duration - cursor >= min_silence:
        gaps.append((cursor, duration))
    return gaps


def jumpcut_plan(
    duration: float, gaps: list[tuple[float, float]], *, speed: float = 1.12
) -> list[tuple[float, float, float]]:
    speed = min(1.15, max(1.08, speed))
    useful = [
        (max(0.0, s), min(duration, e))
        for s, e in gaps
        if min(duration, e) - max(0.0, s) >= 0.25
    ]
    if not useful or duration <= 0:
        return [(0.0, duration, 1.0)]
    plan: list[tuple[float, float, float]] = []
    t = 0.0
    for gs, ge in sorted(useful):
        if gs > t + 0.04:
            plan.append((t, gs, 1.0))
        plan.append((gs, ge, speed))
        t = ge
    if t < duration - 0.04:
        plan.append((t, duration, 1.0))
    return plan or [(0.0, duration, 1.0)]


def plan_is_identity(plan: list[tuple[float, float, float]]) -> bool:
    return all(abs(sp - 1.0) < 0.01 for *_, sp in plan)


def warp_time(t: float, plan: list[tuple[float, float, float]]) -> float:
    acc = 0.0
    for s, e, sp in plan:
        if t <= s:
            return acc
        chunk = min(t, e) - s
        acc += chunk / max(sp, 0.01)
        if t <= e:
            return acc
    return acc


def warped_duration(duration: float, plan: list[tuple[float, float, float]]) -> float:
    return warp_time(duration, plan)


def remap_words(words: list[Word], plan: list[tuple[float, float, float]]) -> list[Word]:
    out: list[Word] = []
    for w in words:
        s = warp_time(w.start, plan)
        e = warp_time(w.end, plan)
        out.append(Word(start=s, end=max(e, s + 0.04), word=w.word, prob=w.prob))
    return out


def keyword_pops(
    words: list[Word], keywords: list[str], *, hold: float = 0.65
) -> list[tuple[float, float, str]]:
    hits: list[tuple[float, float, str]] = []
    if not keywords:
        return hits
    for w in words:
        token = w.word or ""
        for kw in keywords:
            if kw and kw in token:
                hits.append((w.start, w.start + hold, kw))
                break
    return hits[:8]


def drawtext_escape(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("%", "\\%")
        .replace(",", "\\,")
    )


def bgm_mix_filters(speech_label: str, duration: float, *, duck: bool = True) -> tuple[list[str], str]:
    """Assume input 1 is BGM. Returns (filters, audio_label)."""
    filters = [
        f"[1:a]volume=0.16,atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[bgm]"
    ]
    if duck:
        filters.append(f"[{speech_label}]asplit[speech][side]")
        filters.append(
            "[bgm][side]sidechaincompress=threshold=0.08:ratio=6:attack=40:release=280:makeup=2[ducked]"
        )
        filters.append("[speech][ducked]amix=inputs=2:duration=first:dropout_transition=0[amix]")
        return filters, "amix"
    filters.append(f"[{speech_label}][bgm]amix=inputs=2:duration=first:dropout_transition=0[amix]")
    return filters, "amix"


def build_filter_complex(
    spec: StyleSpec,
    *,
    width: int,
    height: int,
    duration: float,
    loudness_i: float,
    ass_filter: str | None,
    font: str | None,
    source_caption: str,
    hook_text: str | None,
    hook_seconds: float,
    punch: tuple[float, float] | None,
    pops: list[tuple[float, float, str]] | None,
    plan: list[tuple[float, float, float]] | None,
    extra_effects: list[dict] | None = None,
    cta_text: str | None = None,
    bgm: bool = False,
) -> tuple[str, str, str]:
    """Return (filter_complex, video_label, audio_label). Input 0 is the trimmed clip."""
    filters: list[str] = []
    v = _vertical(filters, spec.fill, width, height)
    named: list[dict] = list(extra_effects or [])
    have = {(e.get("name") or e.get("type") or "") for e in named}
    for e in style_named_effects(spec, punch=punch, duration=duration):
        if e["name"] not in have:
            named.append(e)
            have.add(e["name"])
    v = apply_named_effects(filters, v, named, width=width, height=height, duration=duration)
    if spec.shake:
        span = punch if punch else (max(0.0, duration * 0.35), min(duration, duration * 0.35 + 0.4))
        filters.append(_shake_chain(v, "vshake", width, height, span[0], span[1]))
        v = "vshake"
    if spec.hook and hook_text and font:
        fs = max(28, int(width * 0.07))
        text = drawtext_escape(hook_text[:24])
        filters.append(
            f"[{v}]drawtext=fontfile='{font}':text='{text}':fontcolor=white:"
            f"fontsize={fs}:x=(w-text_w)/2:y=h*0.11:box=1:boxcolor=black@0.55:"
            f"boxborderw=14:enable='lte(t,{hook_seconds:.2f})'[vhook]"
        )
        v = "vhook"
    if source_caption and font:
        fs = max(16, int(width * 0.032))
        text = drawtext_escape(source_caption[:48])
        filters.append(
            f"[{v}]drawtext=fontfile='{font}':text='{text}':fontcolor=white@0.9:"
            f"fontsize={fs}:x=28:y=h-56:box=1:boxcolor=black@0.4:boxborderw=6[vsrc]"
        )
        v = "vsrc"
    if cta_text and font:
        fs = max(18, int(width * 0.042))
        text = drawtext_escape(cta_text[:24])
        cta_d = min(1.8, duration * 0.5)
        filters.append(
            f"[{v}]drawtext=fontfile='{font}':text='{text}':fontcolor=white:"
            f"fontsize={fs}:x=(w-text_w)/2:y=h*0.80:box=1:boxcolor=0xFE2C55@0.85:"
            f"boxborderw=12:enable='gte(t,{max(0.0, duration - cta_d):.2f})'[vcta]"
        )
        v = "vcta"
    if spec.keyword_pop and pops and font:
        v = _keyword_pops(filters, v, font, width, pops)
    if spec.caption_mask:
        snippet = render_effect(
            "caption_mask", v, "vmask", {"ratio": 0.16}, width=width, height=height, duration=duration
        )
        if snippet:
            filters.append(snippet)
            v = "vmask"
    filters.append(f"[0:a]loudnorm=I={loudness_i}:TP=-1.5:LRA=11[a0]")
    a = "a0"
    if spec.jumpcut and plan and not plan_is_identity(plan):
        jf, v, a = _jumpcut(plan, v, a)
        filters.extend(jf)
        duration = warped_duration(duration, plan)
    if bgm:
        bf, a = bgm_mix_filters(a, duration, duck=True)
        filters.extend(bf)
    if spec.progress and duration > 0.05:
        snippet = render_effect(
            "progress_bar", v, "vbar", {}, width=width, height=height, duration=duration
        )
        if snippet:
            filters.append(snippet)
            v = "vbar"
    if ass_filter:
        filters.append(f"[{v}]{ass_filter}[vout]")
        v = "vout"
    else:
        filters.append(f"[{v}]copy[vout]")
        v = "vout"
    return ";".join(filters), v, a


def _vertical(filters: list[str], fill: str, w: int, h: int) -> str:
    if fill == "crop":
        filters.append(
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[v0]"
        )
        return "v0"
    filters.append("[0:v]split[bg][fg]")
    filters.append(
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:10[blur]"
    )
    filters.append(f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fit]")
    filters.append("[blur][fit]overlay=(W-w)/2:(H-h)/2[v0]")
    return "v0"


def _keyword_pops(
    filters: list[str], v: str, font: str, width: int, pops: list[tuple[float, float, str]]
) -> str:
    fs = max(40, int(width * 0.09))
    label = v
    for i, (ps, pe, text) in enumerate(pops):
        nxt = f"vpop{i}"
        t = drawtext_escape(text[:8])
        filters.append(
            f"[{label}]drawtext=fontfile='{font}':text='{t}':fontcolor=yellow:"
            f"fontsize={fs}:x=(w-text_w)/2:y=(h-text_h)/2-90:borderw=5:"
            f"bordercolor=black:enable='between(t,{ps:.3f},{pe:.3f})'[{nxt}]"
        )
        label = nxt
    return label


def _jumpcut(plan: list[tuple[float, float, float]], vin: str, ain: str) -> tuple[list[str], str, str]:
    filters: list[str] = []
    concat: list[str] = []
    for i, (s, e, sp) in enumerate(plan):
        filters.append(f"[{vin}]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[jt{i}]")
        filters.append(f"[{ain}]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[ja{i}]")
        if abs(sp - 1.0) > 0.01:
            filters.append(f"[jt{i}]setpts=PTS/{sp:.3f}[js{i}]")
            filters.append(f"[ja{i}]atempo={sp:.3f}[jas{i}]")
            concat += [f"[js{i}]", f"[jas{i}]"]
        else:
            concat += [f"[jt{i}]", f"[ja{i}]"]
    n = len(plan)
    filters.append("".join(concat) + f"concat=n={n}:v=1:a=1[jvv][jaa]")
    return filters, "jvv", "jaa"


def xfade_concat(
    clips: list[Path],
    dest: Path,
    *,
    xfade: float = 0.25,
    transition: str = "fadeblack",
) -> Path:
    """Concat rendered clips with xfade. Falls back to concat demuxer if the graph fails."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not clips:
        raise MediaError("没有可拼接的成片")
    if len(clips) == 1:
        shutil.copyfile(clips[0], dest)
        return dest
    ffmpeg = require_ffmpeg()
    durs = [max(0.2, duration_seconds(p)) for p in clips]
    xfade = min(xfade, min(durs) / 2 - 0.05)
    xfade = max(0.08, xfade)
    args: list[str] = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    for p in clips:
        args += ["-i", str(p)]
    filters: list[str] = []
    offset = durs[0] - xfade
    filters.append(
        f"[0:v][1:v]xfade=transition={transition}:duration={xfade:.3f}:offset={offset:.3f}[xv1]"
    )
    filters.append(f"[0:a][1:a]acrossfade=d={xfade:.3f}[xa1]")
    last_v, last_a = "xv1", "xa1"
    acc = durs[0] + durs[1] - xfade
    for i in range(2, len(clips)):
        off = acc - xfade
        nv, na = f"xv{i}", f"xa{i}"
        filters.append(
            f"[{last_v}][{i}:v]xfade=transition={transition}:duration={xfade:.3f}:offset={off:.3f}[{nv}]"
        )
        filters.append(f"[{last_a}][{i}:a]acrossfade=d={xfade:.3f}[{na}]")
        last_v, last_a = nv, na
        acc += durs[i] - xfade
    graph = ";".join(filters)
    args += [
        "-filter_complex", graph,
        "-map", f"[{last_v}]", "-map", f"[{last_a}]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-y", str(dest),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=max(120, acc * 8), check=False)
    if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
        return dest
    # fallback: concat demuxer (hard cut)
    listing = dest.with_suffix(".concat.txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in clips), encoding="utf-8")
    args2 = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy", "-y", str(dest),
    ]
    proc2 = subprocess.run(args2, capture_output=True, text=True, timeout=120, check=False)
    listing.unlink(missing_ok=True)
    if proc2.returncode != 0 or not dest.is_file():
        raise MediaError(f"拼接成片失败: {(proc.stderr or proc2.stderr or '')[-800:]}")
    return dest
