"""Local FastAPI client: dylive ui → http://127.0.0.1:8787"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dylive.config import AppConfig, load_config
from dylive.effects import XFADE_TYPES, effect_catalog
from dylive.exceptions import DyliveError
from dylive.jianying import JIANYING_MAP, jianying_available, jianying_root, write_jianying_draft
from dylive.jobs import get_job, list_jobs
from dylive.logutil import setup_logging

log = logging.getLogger("dylive.server")

WEB_DIR = Path(__file__).resolve().parent / "web"
UI_HOST = "127.0.0.1"
UI_PORT = 8787
UI_URL = f"http://{UI_HOST}:{UI_PORT}"


class RunBody(BaseModel):
    url: str
    dry_run: bool = True
    style: str | None = None
    max_seconds: float | None = None
    title: str | None = None
    punch: bool | None = None
    shake: bool | None = None
    fade: bool | None = None
    mask: bool | None = None
    progress: bool | None = None
    grain: bool | None = None
    glitch: bool | None = None
    vignette: bool | None = None
    xfade: str | None = None
    versions: list[str] | None = None


class OpenBody(BaseModel):
    kind: str = Field(default="clips")  # clips | jianying | recordings
    room: str | None = None


class PublishBody(BaseModel):
    dry_run: bool = True
    title: str | None = None


class LogBroker:
    def __init__(self) -> None:
        self.history: list[str] = []
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def push(self, line: str) -> None:
        with self._lock:
            self.history.append(line)
            if len(self.history) > 2000:
                self.history = self.history[-1500:]
            for q in self._subs:
                try:
                    q.put_nowait(line)
                except queue.Full:
                    pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
            for line in self.history[-200:]:
                try:
                    q.put_nowait(line)
                except queue.Full:
                    break
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)


class QueueLogHandler(logging.Handler):
    def __init__(self, broker: LogBroker) -> None:
        super().__init__()
        self.broker = broker
        self.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.broker.push(self.format(record))
        except Exception:  # noqa: BLE001
            pass


class RunState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.runs: dict[str, dict[str, Any]] = {}


def has_display() -> bool:
    if sys.platform in {"darwin", "win32"}:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def create_app(cfg: AppConfig | None = None) -> FastAPI:
    cfg = cfg or load_config()
    cfg.paths.ensure()
    broker = LogBroker()
    runs = RunState()
    handler = QueueLogHandler(broker)
    root = logging.getLogger("dylive")
    root.addHandler(handler)

    app = FastAPI(title="dylive", docs_url=None, redoc_url=None)
    app.state.cfg = cfg
    app.state.broker = broker
    app.state.runs = runs

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "ui": UI_URL, "jianying": jianying_available(), "version": _version()}

    @app.get("/api/config")
    def config_summary() -> dict[str, Any]:
        from dylive.config import STYLES, XFADES

        c = app.state.cfg
        return {
            "styles": sorted(STYLES),
            "xfades": sorted(XFADES),
            "caption_styles": ["hormozi", "douyin", "standard"],
            "create": {
                "hook": c.create.hook,
                "cta": c.create.cta,
                "voiceover": c.create.voiceover,
                "filler_cut": c.create.filler_cut,
                "versions": c.create.versions,
                "voice": c.create.voice,
            },
        }

    @app.get("/api/effects")
    def effects() -> dict[str, Any]:
        cat = effect_catalog()
        jy = [
            {"name": row["jianying"], "ffmpeg": row["ffmpeg"], "kind": row["kind"]}
            for row in JIANYING_MAP
        ]
        return {
            **cat,
            "jianying": jy,
            "map": JIANYING_MAP,
            "xfade_types": list(XFADE_TYPES),
        }

    @app.get("/api/jobs")
    def jobs() -> dict[str, Any]:
        return {"jobs": list_jobs(app.state.cfg)}

    @app.get("/api/jobs/{room}")
    def job(room: str) -> dict[str, Any]:
        data = get_job(app.state.cfg, room)
        if data is None:
            raise HTTPException(404, f"找不到房间 {room}")
        return data

    @app.post("/api/create/{room}")
    def api_create(room: str) -> dict[str, Any]:
        from dylive.create import create_job

        try:
            payload = create_job(app.state.cfg, room)
        except DyliveError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "create": payload}

    @app.post("/api/run")
    def start_run(body: RunBody) -> dict[str, Any]:
        if not (body.url or "").strip():
            raise HTTPException(400, "请粘贴直播 URL")
        apply_run_overrides(app.state.cfg, body)
        run_id = uuid.uuid4().hex[:10]
        room_guess = _guess_room(body.url)
        info = {
            "id": run_id,
            "room": room_guess,
            "status": "running",
            "url": body.url,
            "dry_run": body.dry_run,
            "error": None,
            "result": None,
            "started": time.time(),
        }
        with runs.lock:
            runs.runs[run_id] = info
        thread = threading.Thread(
            target=_run_thread,
            args=(app.state.cfg, broker, runs, run_id, body),
            daemon=True,
            name=f"dylive-run-{run_id}",
        )
        thread.start()
        broker.push(f"开始流水线 {body.url} dry_run={body.dry_run}")
        return {"ok": True, "run_id": run_id, "room": room_guess}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        with runs.lock:
            info = runs.runs.get(run_id)
        if not info:
            raise HTTPException(404, "找不到这次运行")
        return info

    @app.get("/api/runs/{run_id}/logs")
    def run_logs(run_id: str) -> StreamingResponse:
        return StreamingResponse(_sse(broker), media_type="text/event-stream")

    @app.get("/api/events")
    def events() -> StreamingResponse:
        return StreamingResponse(_sse(broker), media_type="text/event-stream")

    @app.post("/api/jianying/{room}")
    def api_jianying(room: str) -> dict[str, Any]:
        try:
            dest = write_jianying_draft(app.state.cfg, room)
        except DyliveError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "path": str(dest), "hint": "用剪映专业版打开该草稿目录"}

    @app.post("/api/publish/{room}")
    def api_publish(room: str, body: PublishBody | None = None) -> dict[str, Any]:
        body = body or PublishBody()
        from dylive.publish import publish_clips

        job = get_job(app.state.cfg, room)
        if job is None:
            raise HTTPException(404, f"找不到房间 {room}")
        clips = [Path(c["path"]) for c in job.get("clips") or [] if c.get("path") and not c.get("pack")]
        try:
            results = publish_clips(
                app.state.cfg, clips or None, dry_run=body.dry_run, title=body.title
            )
        except DyliveError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "dry_run": body.dry_run, "results": results}

    @app.post("/api/open")
    def api_open(body: OpenBody) -> dict[str, Any]:
        path = _open_path(app.state.cfg, body.kind, body.room)
        if path is None or not path.exists():
            raise HTTPException(404, "目录不存在（先跑一遍流水线）")
        opened = _open_folder(path)
        return {"ok": True, "path": str(path), "opened": opened}

    @app.get("/media/{kind}/{rest:path}")
    def media(kind: str, rest: str) -> FileResponse:
        path = _safe_media(app.state.cfg, kind, rest)
        if path is None:
            raise HTTPException(404, "文件不存在")
        return FileResponse(path)

    if WEB_DIR.is_dir():
        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.exception_handler(DyliveError)
    async def _dylive_err(_: Request, exc: DyliveError) -> JSONResponse:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return app


def apply_run_overrides(cfg: AppConfig, body: RunBody) -> None:
    if body.style:
        cfg.edit.style = body.style
    if body.xfade and body.xfade in XFADE_TYPES:
        cfg.edit.xfade = body.xfade
    if body.max_seconds:
        cfg.record.max_seconds = float(body.max_seconds)
    if body.punch is not None:
        cfg.edit.zoom_punch = body.punch
    if body.shake is not None:
        cfg.edit.shake = body.shake
    if body.fade is not None:
        cfg.edit.fade_in = body.fade
    if body.mask is not None:
        cfg.edit.caption_mask = body.mask
    if body.progress is not None:
        cfg.edit.progress = body.progress
    if body.grain is not None:
        cfg.edit.grain = body.grain
    if body.glitch is not None:
        cfg.edit.glitch = body.glitch
    if body.vignette is not None:
        cfg.edit.vignette = body.vignette
    if body.versions:
        cfg.create.versions = list(body.versions)


def serve_ui(
    cfg: AppConfig | None = None,
    *,
    host: str = UI_HOST,
    port: int = UI_PORT,
    open_browser: bool = True,
) -> None:
    import uvicorn

    cfg = cfg or load_config()
    app = create_app(cfg)
    url = f"http://{host}:{port}"
    log.info("本地客户端 %s", url)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


def _run_thread(cfg: AppConfig, broker: LogBroker, runs: RunState, run_id: str, body: RunBody) -> None:
    from dylive.pipeline import run_pipeline

    try:
        result = run_pipeline(
            cfg,
            body.url,
            dry_run=body.dry_run,
            max_seconds=body.max_seconds,
            title=body.title,
        )
        with runs.lock:
            runs.runs[run_id]["status"] = "done"
            runs.runs[run_id]["result"] = result
            runs.runs[run_id]["room"] = result.get("room") or runs.runs[run_id].get("room")
        broker.push(f"完成 room={result.get('room')} clips={len(result.get('clips') or [])}")
        broker.push(f"打开 {UI_URL} 查看成片")
    except Exception as exc:  # noqa: BLE001
        log.exception("流水线失败")
        with runs.lock:
            runs.runs[run_id]["status"] = "error"
            runs.runs[run_id]["error"] = str(exc)
        broker.push(f"失败: {exc}")


def _sse(broker: LogBroker):
    q = broker.subscribe()
    try:
        yield f"data: {json.dumps({'hello': True}, ensure_ascii=False)}\n\n"
        while True:
            try:
                line = q.get(timeout=15)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            payload = json.dumps({"line": line}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
    finally:
        broker.unsubscribe(q)


def _guess_room(url: str) -> str:
    try:
        from dylive.urls import parse_live_url

        return parse_live_url(url).key
    except Exception:  # noqa: BLE001
        return "unknown"


def _open_path(cfg: AppConfig, kind: str, room: str | None) -> Path | None:
    if kind == "jianying":
        if not room:
            jobs = list_jobs(cfg)
            room = jobs[0]["room"] if jobs else None
        if not room:
            return None
        return jianying_root(cfg, room)
    if kind == "recordings":
        if room:
            return cfg.paths.recordings / room
        return cfg.paths.recordings
    return cfg.paths.output


def _open_folder(path: Path) -> bool:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            import subprocess

            subprocess.Popen(["open", str(path)])
            return True
        import subprocess

        subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("无法打开目录: %s", exc)
        return False


def _safe_media(cfg: AppConfig, kind: str, rest: str) -> Path | None:
    roots = {
        "clips": cfg.paths.output.resolve(),
        "recordings": cfg.paths.recordings.resolve(),
        "jianying": (cfg.paths.output.parent / "jianying").resolve(),
    }
    root = roots.get(kind)
    if root is None:
        return None
    # reject absolute / parent traversal before resolve
    if rest.startswith("/") or rest.startswith("\\") or ".." in Path(rest).parts:
        return None
    dest = (root / rest).resolve()
    try:
        dest.relative_to(root)
    except ValueError:
        return None
    if dest.is_file():
        return dest
    return None


def _version() -> str:
    from dylive import __version__

    return __version__
