"""智能导演：让 DeepSeek v4-pro 判断每条高能片段的最优特效 / 风格 / 文案。

输入片段多维特征（时长、能量/频谱/口播/切镜/关键词/弹幕、转写文本），
输出结构化剪辑决策（style / effects / xfade / caption_style / title / hook / cta /
hashtags / description / reason）。无 LLM key 时降级为启发式规则；任何失败都不阻塞流水线。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dylive.captions import slice_words
from dylive.config import AppConfig
from dylive.create import generate_cta, generate_description, generate_hook, rewrite_script
from dylive.detect import Highlight, highlight_from_dict
from dylive.exceptions import MediaError
from dylive.llm import chat_json, llm_available
from dylive.polish import DEFAULT_HASHTAGS, polish_highlights
from dylive.state import latest_job, read_json, write_json
from dylive.transcribe import load_transcript, transcript_path

log = logging.getLogger("dylive.director")

STYLES = ["douyin_hot", "party", "clean", "cinematic", "vlog"]
EFFECT_KEYS = ["punch", "shake", "jumpcut", "keyword_pop", "progress", "vignette", "grain", "glitch"]
XFADES = ["fade", "fadeblack", "wipeleft", "slideleft", "circlecrop", "slideup"]
CAPTION_STYLES = ["hormozi", "douyin", "standard"]

DIRECTOR_SYSTEM = (
    "你是抖音直播切片的高级剪辑导演。根据高能片段的客观信号判断最优剪辑风格与特效组合，"
    "并生成标题、开场钩子、话题标签与发布文案。口语化、有网感。只输出 JSON。"
)


def _build_prompt(
    f: dict[str, Any], cta: str, guide: str = "", required_tags: list[str] | None = None
) -> str:
    sig = f["signals"]
    guide_block = f"内容方向指导：{guide}\n\n" if guide else ""
    tag_block = (
        f"必带话题（必须原样包含在 hashtags 里）：{' '.join(required_tags)}\n"
        if required_tags
        else ""
    )
    return (
        "下面是一条高能片段的客观特征，请判断最优剪辑方案。\n\n"
        + guide_block
        + tag_block
        + f"时长：{f['duration']:.1f} 秒\n"
        f"综合高能分：{f['score']:.2f}\n"
        f"信号强度：能量 {sig.get('energy', 0):.2f}、频谱 {sig.get('flux', 0):.2f}、"
        f"口播 {sig.get('speech', 0):.2f}、切镜 {sig.get('scene', 0):.2f}、"
        f"关键词 {sig.get('keywords', 0):.2f}、弹幕 {sig.get('chat', 0):.2f}\n"
        f"命中关键词：{'、'.join(f['keywords_hit']) if f['keywords_hit'] else '无'}\n"
        f"弹幕事件数：{f['chat_events']}\n"
        f"口播原文：{f['text'][:120] or '（无转写）'}\n\n"
        f"可选风格 style：{' / '.join(STYLES)}\n"
        f"可选特效 effects（布尔）：{' / '.join(EFFECT_KEYS)}\n"
        f"可选转场 xfade：{' / '.join(XFADES)}\n"
        f"可选字幕 caption_style：{' / '.join(CAPTION_STYLES)}\n\n"
        "决策原则：能量/频谱高、节奏炸 → douyin_hot 或 party，开 punch，可加 shake/glitch；"
        "情绪细腻有故事 → cinematic，开 vignette/grain，关 punch；"
        "生活记录/教程口播 → vlog 或 clean，开 progress，适度 punch；"
        "命中关键词 → 开 keyword_pop；口播有明显停顿 → 开 jumpcut。\n"
        f"结尾引导固定用这句：{cta}\n\n"
        "只输出一个 JSON 对象，不要任何多余文字，字段："
        '{"style":"...","effects":{"punch":true,"shake":false,"jumpcut":false,"keyword_pop":false,'
        '"progress":true,"vignette":false,"grain":false,"glitch":false},'
        '"xfade":"...","caption_style":"...","title":"≤18字","hook":"≤16字开场钩子",'
        '"hashtags":["#xx","#xx"],"description":"≤40字发布文案","reason":"一句话说明为什么这样剪"}'
    )


def direct_clip(
    features: dict[str, Any],
    *,
    cta: str,
    index: int,
    guide: str = "",
    required_tags: list[str] | None = None,
) -> dict[str, Any]:
    """让 LLM 决策单条片段；失败降级启发式。"""
    fallback = _heuristic(features, cta, index)
    if not llm_available():
        return fallback
    decision = chat_json(
        _build_prompt(features, cta, guide=guide, required_tags=required_tags),
        system=DIRECTOR_SYSTEM,
        temperature=0.5,
    )
    if not decision:
        return fallback
    return _merge(decision, fallback)


def _merge_tags(required: list[str], tags: list[str]) -> list[str]:
    """必带话题在前，去重，再拼接 LLM 生成的话题。"""
    out: list[str] = []
    seen: set[str] = set()
    for t in list(required) + list(tags):
        t = str(t).strip()
        if t and t not in seen:
            out.append(t)
            seen.add(t)
    return out


def _merge(d: dict[str, Any], fb: dict[str, Any]) -> dict[str, Any]:
    style = d.get("style") if d.get("style") in STYLES else fb["style"]
    raw = d.get("effects") if isinstance(d.get("effects"), dict) else {}
    effects = {}
    for k in EFFECT_KEYS:
        effects[k] = bool(raw.get(k)) if k in raw else fb["effects"].get(k, False)
    xfade = d.get("xfade") if d.get("xfade") in XFADES else fb["xfade"]
    caption_style = d.get("caption_style") if d.get("caption_style") in CAPTION_STYLES else fb["caption_style"]
    title = (str(d.get("title") or "").strip()[:24]) or fb["title"]
    hook = (str(d.get("hook") or "").strip()[:20]) or fb["hook"]
    hashtags = [
        t for t in (d.get("hashtags") or [])
        if isinstance(t, str) and t.startswith("#")
    ][:5] or fb["hashtags"]
    description = (str(d.get("description") or "").strip()[:60]) or fb["description"]
    reason = (str(d.get("reason") or "").strip()[:120]) or fb["reason"]
    return {
        "style": style,
        "effects": effects,
        "xfade": xfade,
        "caption_style": caption_style,
        "title": title,
        "hook": hook,
        "cta": fb["cta"],
        "hashtags": hashtags,
        "description": description,
        "reason": reason,
    }


def _heuristic(f: dict[str, Any], cta: str, index: int) -> dict[str, Any]:
    sig = f["signals"]
    energy = float(sig.get("energy", 0) or 0)
    flux = float(sig.get("flux", 0) or 0)
    speech = float(sig.get("speech", 0) or 0)
    kw = bool(f["keywords_hit"])
    if speech < 0.3:
        style = "cinematic"
    elif energy > 0.6 and flux > 0.6:
        style = "party"
    elif energy > 0.4 or kw:
        style = "douyin_hot"
    else:
        style = "clean"
    effects = {
        "punch": energy > 0.4,
        "shake": energy > 0.7,
        "jumpcut": False,
        "keyword_pop": kw,
        "progress": style in ("douyin_hot", "vlog", "clean"),
        "vignette": style == "cinematic",
        "grain": style in ("cinematic", "vlog"),
        "glitch": style == "party",
    }
    title = rewrite_script(f["text"]) or "高能"
    hook = generate_hook(f["text"], index=index)
    hashtags = list(DEFAULT_HASHTAGS)
    return {
        "style": style,
        "effects": effects,
        "xfade": "fadeblack",
        "caption_style": "standard" if style == "cinematic" else "douyin",
        "title": title,
        "hook": hook,
        "cta": cta,
        "hashtags": hashtags,
        "description": generate_description(title, hashtags),
        "reason": f"启发式降级：能量{energy:.2f} 频谱{flux:.2f}",
    }


def _features(cfg: AppConfig, h: Highlight, transcript, job_key: str) -> dict[str, Any]:
    words_all = transcript.words if transcript else []
    cw = slice_words(words_all, h.start, h.end, origin=h.start) if words_all else []
    text = "".join((w.word or "") for w in cw)
    hit = [k for k in cfg.detect.keywords if k and k in text]
    sig = dict(h.why or {})
    return {
        "duration": h.end - h.start,
        "score": h.score,
        "signals": sig,
        "keywords_hit": hit,
        "chat_events": int(round(float(sig.get("chat", 0) or 0) * 10)),
        "text": text,
    }


def director_job(cfg: AppConfig, source: str | Path | None = None) -> dict[str, Any]:
    """导演决策主入口：生成 director.json（逐条剪辑决策 + 全局 CTA）。"""
    cfg.paths.ensure()
    media, highlights, job_key = _load(cfg, source)
    tr_file = transcript_path(cfg, job_key)
    transcript = load_transcript(tr_file) if tr_file.is_file() else None
    highs = polish_highlights(highlights, transcript, room_id=job_key)
    act = cfg.activity
    required = [t.strip() for t in act.hashtags if t and t.strip()] if act.enabled else []
    guide = act.content_guide.strip() if act.enabled else ""
    cta = (cfg.create.cta_text or "").strip() or generate_cta(room_id=job_key)
    clips: list[dict[str, Any]] = []
    for i, h in enumerate(highs):
        f = _features(cfg, h, transcript, job_key)
        d = direct_clip(f, cta=cta, index=i, guide=guide, required_tags=required)
        d["hashtags"] = _merge_tags(required, d.get("hashtags") or [])
        d["publish_text"] = (d["title"] + " " + " ".join(d["hashtags"])).strip()
        d.update({"index": i, "start": h.start, "end": h.end, "score": h.score})
        clips.append(d)
        h.title = d["title"]
        h.hook = d["hook"]
        h.hashtags = d["hashtags"]
    payload = {"media": str(media), "cta": cta, "clips": clips}
    write_json(cfg.paths.data / "jobs" / job_key / "director.json", payload)
    log.info("导演决策完成 room=%s clips=%s", job_key, len(clips))
    return payload


def load_director(cfg: AppConfig, job_key: str) -> dict[str, Any] | None:
    path = cfg.paths.data / "jobs" / job_key / "director.json"
    if path.is_file():
        return read_json(path)
    return None


def _load(cfg: AppConfig, source: str | Path | None) -> tuple[Path, list, str]:
    if source:
        path = Path(source)
        if path.is_file() and path.suffix.lower() == ".json":
            return _from_json(path)
        if path.is_dir() and (path / "highlights.json").is_file():
            return _from_json(path / "highlights.json")
        job_json = cfg.paths.data / "jobs" / str(source) / "highlights.json"
        if job_json.is_file():
            return _from_json(job_json)
    job = latest_job(cfg)
    if job and (job / "highlights.json").is_file():
        return _from_json(job / "highlights.json")
    raise MediaError("没有 highlights.json，先运行 dylive detect")


def _from_json(path: Path) -> tuple[Path, list, str]:
    payload = read_json(path)
    media = Path(payload["media"])
    highs = [highlight_from_dict(row) for row in payload.get("highlights") or []]
    return media, highs, path.parent.name
