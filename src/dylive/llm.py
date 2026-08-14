"""统一 LLM 客户端：DeepSeek（OpenAI 兼容）+ 可选多模态（豆包 Vision）。

- deepseek-v4-pro：推理型，用于「导演决策」（判断最优特效/风格/文案）。
- deepseek-v4-flash：快速型，用于文案改写等轻量任务。
- 推理模型会返回 reasoning_content，这里只取 content 作为最终结果。
- 多模态走独立 vision 客户端（火山方舟豆包，OpenAI 兼容），未配 key 自动跳过。
- 所有函数失败均返回 None，由调用方降级，绝不中断主流水线。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

log = logging.getLogger("dylive.llm")

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_FAST_MODEL = "deepseek-v4-flash"

DEFAULT_VISION_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_VISION_MODEL = "doubao-seed-2-1-pro-260628"


def llm_api_key() -> str:
    return (
        os.environ.get("DYLIVE_LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()


def llm_base_url() -> str:
    return (os.environ.get("DYLIVE_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def llm_model() -> str:
    return os.environ.get("DYLIVE_LLM_MODEL") or DEFAULT_MODEL


def fast_model() -> str:
    return os.environ.get("DYLIVE_LLM_FAST_MODEL") or DEFAULT_FAST_MODEL


def vision_api_key() -> str:
    return (os.environ.get("DYLIVE_VISION_API_KEY") or "").strip()


def vision_base_url() -> str:
    return (os.environ.get("DYLIVE_VISION_BASE_URL") or DEFAULT_VISION_BASE_URL).rstrip("/")


def vision_model() -> str:
    return os.environ.get("DYLIVE_VISION_MODEL") or DEFAULT_VISION_MODEL


def llm_available() -> bool:
    return bool(llm_api_key())


def vision_available() -> bool:
    return bool(vision_api_key())


def chat(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0.6,
    max_tokens: int = 2048,
    json_mode: bool = False,
) -> str | None:
    """OpenAI 兼容对话；失败返回 None，由调用方降级。"""
    import httpx

    key = llm_api_key()
    if not key:
        return None
    payload: dict[str, Any] = {
        "model": model or llm_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                llm_base_url() + "/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            if content:
                return content
            return (msg.get("reasoning_content") or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        log.info("LLM 调用失败，降级 (%s)", exc)
        return None


def chat_json(
    prompt: str,
    *,
    system: str = "你是抖音切片编导。",
    model: str | None = None,
    temperature: float = 0.4,
) -> dict[str, Any] | None:
    text = chat(
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        model=model,
        temperature=temperature,
    )
    if not text:
        return None
    return _extract_json(text)


def vision(prompt: str, image_base64: str, *, mime: str = "image/jpeg") -> str | None:
    """多模态视觉理解（豆包 Vision）；未配置 key 返回 None。"""
    import httpx

    key = vision_api_key()
    if not key:
        return None
    payload = {
        "model": vision_model(),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_base64}"},
                    },
                ],
            }
        ],
        "max_tokens": 1024,
    }
    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                vision_base_url() + "/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"].get("content") or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        log.info("Vision 调用失败，跳过 (%s)", exc)
        return None


def _extract_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"(\{.*\})", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None
