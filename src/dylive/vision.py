"""可选的多模态视觉增强：抽帧 + 豆包 Vision 评分，选出最佳封面帧。

未配置 DYLIVE_VISION_API_KEY 时全部跳过，不影响主流程。
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from dylive.llm import vision, vision_available
from dylive.media import require_ffmpeg
from dylive.state import write_json

log = logging.getLogger("dylive.vision")


def extract_frames(media: Path, out_dir: Path, at: float, *, count: int = 3) -> list[Path]:
    """在高能片段起点附近抽 count 帧。"""
    ffmpeg = require_ffmpeg()
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for i in range(count):
        t = max(0.0, at + i * 0.8)
        out = out_dir / f"f{int(t)}_{i}.jpg"
        args = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", f"{t:.2f}", "-i", str(media),
            "-frames:v", "1", "-q:v", "3", "-y", str(out),
        ]
        proc = subprocess.run(args, capture_output=True, check=False)
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 0:
            frames.append(out)
    return frames


def score_frame(path: Path) -> float | None:
    """让豆包 Vision 给画面打分（0-1），判断封面冲击力；失败返回 None。"""
    if not vision_available():
        return None
    data = base64.b64encode(path.read_bytes()).decode()
    prompt = (
        "你是抖音切片封面评审。给这张直播截图打分（0 到 1），判断它作为高能切片封面的"
        '视觉冲击力与吸引力。只输出 JSON：{"score": 0.5, "label": "一句话"}'
    )
    text = vision(prompt, data)
    if not text:
        return None
    m = text.find("{")
    if m == -1:
        return None
    try:
        obj = json.loads(text[m:])
        return max(0.0, min(1.0, float(obj.get("score", 0.5))))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def pick_covers(cfg, media: Path, highlights: list, job_key: str) -> list[dict[str, Any]]:
    """为每条高能片段选最佳封面帧，写 cover.json，返回封面列表。"""
    if not vision_available():
        return []
    out_dir = cfg.paths.data / "jobs" / job_key / "frames"
    covers: list[dict[str, Any]] = []
    for i, h in enumerate(highlights):
        frames = extract_frames(media, out_dir, at=h.start + 1.0, count=3)
        best, best_score = None, 0.0
        for fr in frames:
            s = score_frame(fr)
            if s is not None and s > best_score:
                best, best_score = fr, s
        if best is not None:
            covers.append({
                "index": i, "start": h.start, "end": h.end,
                "cover": str(best), "score": round(best_score, 3),
            })
    if covers:
        write_json(cfg.paths.data / "jobs" / job_key / "cover.json", {"covers": covers})
        log.info("视觉封面完成 room=%s covers=%s", job_key, len(covers))
    return covers
