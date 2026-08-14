"""Job directory layout so staged commands can pick up where the last left off."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dylive.config import AppConfig


def job_dir(cfg: AppConfig, key: str) -> Path:
    path = cfg.paths.data / "jobs" / _safe(key)
    path.mkdir(parents=True, exist_ok=True)
    return path


def recording_dir(cfg: AppConfig, key: str) -> Path:
    path = cfg.paths.recordings / _safe(key)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_job(cfg: AppConfig) -> Path | None:
    root = cfg.paths.data / "jobs"
    if not root.exists():
        return None
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def _safe(key: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    return cleaned or "unknown"
