"""Shared watch→publish pipeline used by the CLI and the local UI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dylive.config import AppConfig

log = logging.getLogger("dylive.pipeline")


def run_pipeline(
    cfg: AppConfig,
    url: str,
    *,
    dry_run: bool = True,
    max_seconds: float | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    from dylive.detect import detect_job
    from dylive.edit import compile_job, edit_job
    from dylive.publish import publish_clips
    from dylive.record import record_url
    from dylive.transcribe import transcribe_job

    dest = record_url(cfg, url, wait=True, max_seconds=max_seconds)
    log.info("recordings: %s", dest)
    media, tr = transcribe_job(cfg, dest)
    log.info("transcript: %s words  media=%s", len(tr.words), media)
    media, highs = detect_job(cfg, dest)
    log.info("highlights: %s  media=%s", len(highs), media)
    # 二次创作：文案改写 / 钩子 / CTA / 解说稿 / 剪口播 / 可选配音
    try:
        from dylive.create import create_job

        create_job(cfg, dest.name)
    except Exception as exc:  # noqa: BLE001
        log.warning("二次创作跳过: %s", exc)
    # 导演决策：DeepSeek v4-pro 判断每条片段的最优特效/风格/文案
    try:
        from dylive.director import director_job

        director_job(cfg, dest.name)
    except Exception as exc:  # noqa: BLE001
        log.warning("导演决策跳过: %s", exc)
    # 可选：豆包 Vision 抽帧选封面（未配 key 自动跳过）
    try:
        from dylive.vision import pick_covers

        pick_covers(cfg, media, highs, dest.name)
    except Exception as exc:  # noqa: BLE001
        log.warning("视觉封面跳过: %s", exc)
    clips = edit_job(cfg, dest.name, title=title, room_id=dest.name)
    for c in clips:
        log.info("clip: %s", c)
    pack: Path | None = None
    try:
        pack = compile_job(cfg, dest.name)
        log.info("pack: %s", pack)
    except Exception as exc:  # noqa: BLE001
        log.warning("合集跳过: %s", exc)
    pub_title = title or (highs[0].title if highs else None)
    publish_clips(cfg, clips, dry_run=dry_run, title=pub_title)
    return {
        "room": dest.name,
        "clips": [str(p) for p in clips],
        "pack": str(pack) if pack else None,
        "highlights": len(highs),
        "dry_run": dry_run,
    }
