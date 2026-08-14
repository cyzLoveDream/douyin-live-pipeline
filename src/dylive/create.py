"""二次创作引擎 (0.4.0)：文案改写、开场钩子、结尾引导、解说稿、配音、剪口播删词。

直播切片以「原生语音」为主——高能本身是主播在讲，配音只用于附加的解说/钩子/CTA，
并支持多版本输出（原声版 / 解说版 / 多风格）。本模块在 detect 之后、edit 之前运行，
产出 data/jobs/<room>/create.json。无 LLM key 时全部降级为启发式；无 edge-tts 时
跳过配音；任何失败都不阻塞主流水线。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from dylive.captions import slice_words
from dylive.config import AppConfig
from dylive.detect import highlight_from_dict
from dylive.exceptions import MediaError
from dylive.media import require_ffmpeg
from dylive.polish import DEFAULT_HASHTAGS, polish_highlights
from dylive.state import latest_job, read_json, write_json
from dylive.transcribe import Word, load_transcript, transcript_path

log = logging.getLogger("dylive.create")

FILLER_WORDS = {
    "嗯", "嗯嗯", "啊", "呃", "哦", "诶", "唔", "哈", "那个", "就是", "然后",
    "然后呢", "这个", "就是说", "怎么说", "这样子", "那么", "其实", "反正",
    "然后就是", "对吧", "对不对", "你知道吗", "说白了",
}

CTA_TEMPLATES = [
    "关注主播，下播前还有高能！",
    "喜欢就点关注，每天更新高能切片",
    "关注不迷路，下次开播第一时间看到",
]

HOOK_TEMPLATES = [
    "前方高能，别眨眼！",
    "这段太炸了，看到最后",
    "就这一下，全场都安静了",
]


def _api_key() -> str:
    return (os.environ.get("DYLIVE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()


def _llm_chat(prompt: str, *, system: str = "你是抖音切片编导。", temperature: float = 0.4) -> str | None:
    """调用 OpenAI 兼容接口；失败返回 None，调用方降级为启发式。"""
    import httpx

    key = _api_key()
    if not key:
        return None
    base = (os.environ.get("DYLIVE_LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("DYLIVE_LLM_MODEL") or "gpt-4o-mini"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                base + "/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        log.info("LLM 不可用，降级启发式 (%s)", exc)
        return None


def _extract_json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
        return None


def filter_filler_words(words: list[Word]) -> list[Word]:
    """剪口播：去掉语气词 / 口误，字幕和检测都不再算它们。"""
    cleaned: list[Word] = []
    for w in words:
        token = (w.word or "").strip()
        if token and token not in FILLER_WORDS:
            cleaned.append(w)
    return cleaned


def filler_intervals(words: list[Word]) -> list[tuple[float, float]]:
    """语气词所在时间区间（供可选的物理剪除）。"""
    out: list[tuple[float, float]] = []
    for w in words:
        if (w.word or "").strip() in FILLER_WORDS:
            out.append((w.start, w.end))
    return out


def _clip_text(words: list[Word], *, max_chars: int = 80) -> str:
    return "".join(w.word for w in words)[:max_chars]


def rewrite_script(text: str, *, room_id: str | None = None) -> str:
    """文案改写：把口播原文改写成更精炼的文案；无 LLM 时返回原文首句。"""
    text = (text or "").strip()
    if not text:
        return f"{room_id or '直播'}高能"
    prompt = (
        "把这段直播口播改写成一条≤18字的抖音文案，突出高能和钩子，口语化、有网感，"
        f"只输出文案本身：\n{text}"
    )
    out = _llm_chat(prompt, system="你是抖音切片编导，只输出文案。")
    if out and out.strip():
        return out.strip()[:24]
    return text[:20]


def generate_hook(text: str, *, room_id: str | None = None, index: int = 0) -> str:
    """开场钩子（文字花字，用原声，不配音）。"""
    text = (text or "").strip()
    prompt = f"给这段直播切片写一句≤16字的开场钩子，制造悬念/情绪，只输出钩子：\n{text}"
    out = _llm_chat(prompt, system="你是抖音切片编导，只输出钩子。")
    if out and out.strip():
        return out.strip()[:20]
    if text:
        return text[:16]
    return HOOK_TEMPLATES[index % len(HOOK_TEMPLATES)]


def generate_cta(*, room_id: str | None = None) -> str:
    """结尾引导（关注/点赞/进直播间）。"""
    prompt = "写一句≤14字的抖音直播切片结尾引导（引导关注/点赞/进直播间），只输出这一句。"
    out = _llm_chat(prompt, system="你是抖音切片编导，只输出引导语。")
    if out and out.strip():
        return out.strip()[:20]
    return CTA_TEMPLATES[0]


def generate_narration(clip_text: str, *, title: str, room_id: str | None = None) -> str:
    """解说稿：一句引导式旁白，叠加在原声之上（不是替换原声）。"""
    clip_text = (clip_text or "").strip()
    prompt = (
        "给这条抖音切片写一句≤20字的解说旁白，引导观众进入高能点，可用「家人们/注意看」这类口播，"
        f"只输出旁白。切片内容：{clip_text}"
    )
    out = _llm_chat(prompt, system="你是抖音解说配音文案，只输出旁白。")
    if out and out.strip():
        return out.strip()[:24]
    return f"{title}，前方高能！"[:24]


def generate_description(title: str, tags: list[str], *, room_id: str | None = None) -> str:
    """发布文案（描述）。"""
    title = title or f"{room_id or '直播'}高能"
    tags = [t for t in (tags or []) if str(t).startswith("#")]
    prompt = (
        f"给这条抖音切片写一条≤40字的发布文案（含话题但别太多），只输出文案：\n"
        f"标题：{title}\n话题：{' '.join(tags)}"
    )
    out = _llm_chat(prompt, system="你是抖音运营，只输出发布文案。")
    if out and out.strip():
        return out.strip()[:60]
    tag_str = " ".join(tags[:3])
    return f"{title} {tag_str}".strip()[:60]


def voice_available() -> bool:
    try:
        import edge_tts  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def synthesize_voice(
    text: str, out_path: Path, *, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+0%"
) -> Path | None:
    """用 edge-tts 合成配音；未安装 / 失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        import asyncio

        import edge_tts

        out_path.parent.mkdir(parents=True, exist_ok=True)
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        asyncio.run(communicate.save(str(out_path)))
        if out_path.is_file() and out_path.stat().st_size > 0:
            return out_path
    except Exception as exc:  # noqa: BLE001
        log.info("配音失败（跳过）：%s", exc)
    out_path.unlink(missing_ok=True)
    return None


def mix_voiceover(clip: Path, voice: Path, dest: Path, *, duck: float = 0.18) -> Path:
    """把解说配音混入成片（压低原声、保留主播声音），返回新文件。"""
    ffmpeg = require_ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(clip),
        "-i",
        str(voice),
        "-filter_complex",
        "[0:a]asplit[a0][side];"
        "[1:a]volume=1.0[a1];"
        "[a1][side]sidechaincompress=threshold=0.05:ratio=5:attack=40:release=260:makeup=2[ducked];"
        "[a0][ducked]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.9[aout]",
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        "-y",
        str(dest),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode != 0 or not dest.is_file():
        raise MediaError(f"配音混音失败: {(proc.stderr or '')[-500:]}")
    return dest


def create_job(cfg: AppConfig, source: str | Path | None = None) -> dict[str, Any]:
    """二次创作主入口：生成 create.json（逐条钩子/文案/解说稿 + 全局 CTA/发布文案），可选配音。"""
    cfg.paths.ensure()
    media, highlights, job_key = _load(cfg, source)
    tr_file = transcript_path(cfg, job_key)
    transcript = load_transcript(tr_file) if tr_file.is_file() else None
    words_all = transcript.words if transcript else []

    highs = polish_highlights(highlights, transcript, room_id=job_key)
    cc = cfg.create

    cta = (cc.cta_text or "").strip() or generate_cta(room_id=job_key)
    clips_out: list[dict[str, Any]] = []
    for i, h in enumerate(highs):
        cw = slice_words(words_all, h.start, h.end, origin=h.start) if words_all else []
        cleaned = filter_filler_words(cw)
        text = _clip_text(cleaned)
        hook = h.hook or generate_hook(text, room_id=job_key, index=i)
        title = h.title or rewrite_script(text, room_id=job_key)
        description = generate_description(
            title, h.hashtags or list(DEFAULT_HASHTAGS), room_id=job_key
        )
        narration = (cc.narration_text or "").strip() or generate_narration(
            text, title=title, room_id=job_key
        )
        h.hook = h.hook or hook
        h.title = h.title or title
        clips_out.append(
            {
                "index": i,
                "start": h.start,
                "end": h.end,
                "score": h.score,
                "title": title,
                "hook": hook,
                "hashtags": h.hashtags or [],
                "description": description,
                "narration": narration,
                "filler_cut": len(cw) - len(cleaned),
            }
        )

    payload: dict[str, Any] = {
        "media": str(media),
        "cta": cta,
        "description": generate_description(
            (highs[0].title if highs else None), DEFAULT_HASHTAGS, room_id=job_key
        ),
        "narration": cc.narration_text or None,
        "voice": cc.voice,
        "rate": cc.rate,
        "clips": clips_out,
    }
    dest = cfg.paths.data / "jobs" / job_key / "create.json"
    write_json(dest, payload)

    voice_dir = cfg.paths.output.parent / "voice" / job_key
    if cc.voiceover and voice_available():
        for item in clips_out:
            vp = voice_dir / f"{job_key}_{item['index'] + 1:02d}.mp3"
            made = synthesize_voice(item["narration"], vp, voice=cc.voice, rate=cc.rate)
            item["voice"] = str(made) if made else None
        write_json(dest, payload)
    else:
        for item in clips_out:
            item["voice"] = None

    log.info("二次创作完成 room=%s clips=%s cta=%s", job_key, len(clips_out), cta)
    return payload


def load_create(cfg: AppConfig, job_key: str) -> dict[str, Any] | None:
    path = cfg.paths.data / "jobs" / job_key / "create.json"
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
