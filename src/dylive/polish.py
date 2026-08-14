"""Optional LLM polish for titles / hashtags / hook. Never fails the pipeline.

If OPENAI_API_KEY or DYLIVE_LLM_API_KEY is set (plus optional DYLIVE_LLM_BASE_URL),
re-rank-ish copywriting runs after transcribe. Otherwise heuristic titles from
the first high-score sentence. No Coze / DashScope hard dependency.
"""

from __future__ import annotations

import json
import logging
import os
import re

from dylive.captions import first_sentence, slice_words
from dylive.detect import Highlight
from dylive.transcribe import Transcript

log = logging.getLogger("dylive.polish")

DEFAULT_HASHTAGS = ["#直播切片", "#高能", "#抖音"]


def polish_highlights(
    highlights: list[Highlight],
    transcript: Transcript | None,
    *,
    room_id: str | None = None,
) -> list[Highlight]:
    if not highlights:
        return highlights
    try:
        if _api_key():
            return _llm_polish(highlights, transcript, room_id=room_id)
    except Exception as exc:  # noqa: BLE001
        log.info("LLM 文案不可用，改用启发式 (%s)", exc)
    return _heuristic_polish(highlights, transcript, room_id=room_id)


def _api_key() -> str:
    return (os.environ.get("DYLIVE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()


def _heuristic_polish(
    highlights: list[Highlight],
    transcript: Transcript | None,
    *,
    room_id: str | None,
) -> list[Highlight]:
    words_all = transcript.words if transcript else []
    for i, h in enumerate(highlights):
        clip_words = slice_words(words_all, h.start, h.end, origin=h.start) if words_all else []
        sentence = first_sentence(clip_words, max_chars=16) if clip_words else ""
        title = (sentence or f"{room_id or '直播'}高能").replace("\n", "")[:20]
        tags = list(DEFAULT_HASHTAGS)
        blob = "".join(w.word for w in clip_words)
        if any(k in blob for k in ("买它", "秒杀", "免费", "送给")):
            tags.append("#带货")
        if "哈哈" in blob or "绝了" in blob:
            tags.append("#搞笑")
        h.title = h.title or title
        h.hashtags = h.hashtags or tags[:5]
        h.hook = h.hook or (sentence or title)
        if i == 0:
            log.info("启发式标题: %s %s", h.title, " ".join(h.hashtags))
    return highlights


def _llm_polish(
    highlights: list[Highlight],
    transcript: Transcript | None,
    *,
    room_id: str | None,
) -> list[Highlight]:
    import httpx

    key = _api_key()
    base = (os.environ.get("DYLIVE_LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("DYLIVE_LLM_MODEL") or "gpt-4o-mini"
    snippets = []
    words_all = transcript.words if transcript else []
    for i, h in enumerate(highlights[:8]):
        clip_words = slice_words(words_all, h.start, h.end, origin=h.start) if words_all else []
        text = "".join(w.word for w in clip_words)[:80]
        snippets.append(
            {"i": i, "start": h.start, "end": h.end, "score": h.score, "why": h.why, "text": text}
        )
    prompt = (
        "你是抖音切片编导。根据口播片段生成 JSON 数组，每项："
        '{"i":0,"title":"≤18字标题","hashtags":["#a","#b","#c"],"hook":"≤16字钩子"}。'
        "只输出 JSON。素材：\n"
        + json.dumps(snippets, ensure_ascii=False)
    )
    url = base + "/chat/completions"
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0.4,
                "messages": [
                    {"role": "system", "content": "只输出 JSON。不要解释。"},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    data = _extract_json(content)
    rows = data if isinstance(data, list) else data.get("items") or data.get("clips") or []
    by_i = {int(r.get("i", k)): r for k, r in enumerate(rows) if isinstance(r, dict)}
    for i, h in enumerate(highlights):
        row = by_i.get(i) or {}
        h.title = str(row.get("title") or h.title or "")[:24]
        tags = row.get("hashtags") or []
        if isinstance(tags, str):
            tags = [t for t in tags.split() if t.startswith("#")]
        h.hashtags = [str(t) if str(t).startswith("#") else f"#{t}" for t in tags][:5] or h.hashtags
        h.hook = str(row.get("hook") or h.hook or "")[:20]
    # Fill any blanks with heuristics so publish metadata is never empty.
    return _heuristic_polish(highlights, transcript, room_id=room_id)


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
            return json.loads(m.group(1))
        raise
