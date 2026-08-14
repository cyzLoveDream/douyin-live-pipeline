"""Scan data/jobs for the local UI / API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dylive.config import AppConfig
from dylive.state import read_json

STAGES = ("watch", "record", "transcribe", "detect", "create", "edit", "compile")

STAGE_FILE = {
    "watch": "watch.json",
    "record": "record.json",
    "transcribe": "transcript.json",
    "detect": "highlights.json",
    "create": "create.json",
    "edit": "edit.json",
}


def list_jobs(cfg: AppConfig) -> list[dict[str, Any]]:
    root = cfg.paths.data / "jobs"
    if not root.is_dir():
        return []
    jobs = [summarize_job(cfg, p) for p in root.iterdir() if p.is_dir()]
    jobs.sort(key=lambda j: j.get("mtime") or 0, reverse=True)
    return jobs


def summarize_job(cfg: AppConfig, job: Path) -> dict[str, Any]:
    stages = stage_status(cfg, job)
    clips = clip_entries(cfg, job)
    highs = highlight_entries(job)
    return {
        "room": job.name,
        "path": str(job),
        "mtime": job.stat().st_mtime,
        "stages": stages,
        "clips": clips,
        "highlights": highs,
        "current": _current_stage(stages),
    }


def get_job(cfg: AppConfig, room: str) -> dict[str, Any] | None:
    job = cfg.paths.data / "jobs" / room
    if not job.is_dir():
        return None
    return summarize_job(cfg, job)


def stage_status(cfg: AppConfig, job: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in STAGES:
        if name == "compile":
            pack = cfg.paths.output / f"{job.name}_pack.mp4"
            out[name] = "done" if pack.is_file() else "pending"
            continue
        fname = STAGE_FILE[name]
        out[name] = "done" if (job / fname).is_file() else "pending"
    return out


def clip_entries(cfg: AppConfig, job: Path) -> list[dict[str, Any]]:
    clips: list[dict[str, Any]] = []
    edit_json = job / "edit.json"
    paths: list[Path] = []
    if edit_json.is_file():
        data = read_json(edit_json)
        paths = [Path(p) for p in data.get("clips") or []]
    if not paths and cfg.paths.output.is_dir():
        paths = sorted(
            p for p in cfg.paths.output.glob(f"{job.name}_*.mp4") if "_pack" not in p.name
        )
    for p in paths:
        if not p.is_file():
            continue
        rel = _media_rel(cfg, p)
        clips.append(
            {
                "path": str(p),
                "name": p.name,
                "url": f"/media/{rel}" if rel else None,
                "size": p.stat().st_size,
            }
        )
    pack = cfg.paths.output / f"{job.name}_pack.mp4"
    if pack.is_file():
        rel = _media_rel(cfg, pack)
        clips.append(
            {
                "path": str(pack),
                "name": pack.name,
                "url": f"/media/{rel}" if rel else None,
                "size": pack.stat().st_size,
                "pack": True,
            }
        )
    return clips


def highlight_entries(job: Path) -> list[dict[str, Any]]:
    path = job / "highlights.json"
    if not path.is_file():
        return []
    data = read_json(path)
    words = _words(job)
    out: list[dict[str, Any]] = []
    for row in data.get("highlights") or []:
        start = float(row.get("start") or 0)
        end = float(row.get("end") or 0)
        snippet = _snippet(words, start, end)
        out.append(
            {
                "start": start,
                "end": end,
                "score": float(row.get("score") or 0),
                "title": row.get("title") or "",
                "hook": row.get("hook") or "",
                "hashtags": row.get("hashtags") or [],
                "why": row.get("why") or {},
                "snippet": snippet,
            }
        )
    return out


def _current_stage(stages: dict[str, str]) -> str:
    last = "watch"
    for name in STAGES:
        if stages.get(name) == "done":
            last = name
        else:
            break
    return last


def _words(job: Path) -> list[dict[str, Any]]:
    path = job / "transcript.json"
    if not path.is_file():
        return []
    data = read_json(path)
    words: list[dict[str, Any]] = []
    for seg in data.get("segments") or []:
        words.extend(seg.get("words") or [])
    return words


def _snippet(words: list[dict[str, Any]], start: float, end: float, *, limit: int = 36) -> str:
    parts = []
    for w in words:
        ws = float(w.get("start") or 0)
        we = float(w.get("end") or 0)
        if we < start or ws > end:
            continue
        tok = str(w.get("word") or "").strip()
        if tok:
            parts.append(tok)
    text = "".join(parts)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _media_rel(cfg: AppConfig, path: Path) -> str | None:
    path = path.resolve()
    roots = {
        "clips": cfg.paths.output.resolve(),
        "recordings": cfg.paths.recordings.resolve(),
        "jianying": (cfg.paths.output.parent / "jianying").resolve(),
    }
    for kind, root in roots.items():
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        return f"{kind}/{rel.as_posix()}"
    return None
