"""Record a live room with yt-dlp and/or ffmpeg into segmented files."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

from dylive.config import AppConfig
from dylive.exceptions import MediaError, NeedAccessError, NotLiveError
from dylive.httputil import build_client
from dylive.media import require_ffmpeg, require_ytdlp
from dylive.state import recording_dir, write_json
from dylive.watch import RoomStatus, fetch_room, resolve_target, wait_until_live

log = logging.getLogger("dylive.record")


def record_url(
    cfg: AppConfig,
    url: str,
    *,
    wait: bool = True,
    max_seconds: float | None = None,
) -> Path:
    """Record until the live ends, max_seconds, or a drop that cannot be resumed."""
    cfg.paths.ensure()
    client = build_client(cfg)
    try:
        if wait:
            status = wait_until_live(cfg, url)
            target = status.target
        else:
            target = resolve_target(cfg, url, client)
            status = fetch_room(cfg, target, client)
            if not status.is_live:
                raise NotLiveError(f"未开播: {target.key}")
    finally:
        client.close()

    dest = recording_dir(cfg, target.key)
    cap = max_seconds if max_seconds is not None else cfg.record.max_seconds
    started = time.time()
    files: list[str] = []
    session = 0

    log.info("开始录制 %s → %s", target.watch_url, dest)
    while True:
        elapsed = time.time() - started
        if cap and elapsed >= cap:
            log.info("达到 max_seconds=%.0f，停止录制", cap)
            break
        client = build_client(cfg)
        try:
            status = fetch_room(cfg, target, client)
        except NeedAccessError:
            raise
        finally:
            client.close()
        if not status.is_live:
            if files:
                log.info("直播结束，已录 %s 个分段", len(files))
                break
            raise NotLiveError(f"未开播: {target.key}")

        remaining = None if not cap else max(1.0, cap - (time.time() - started))
        session += 1
        try:
            produced = _record_session(cfg, status, dest, session, remaining)
            files.extend(str(p) for p in produced)
        except MediaError as exc:
            log.warning("录制中断: %s；%ss 后重试", exc, cfg.record.resume_gap_seconds)
            time.sleep(cfg.record.resume_gap_seconds)
            continue

        if cap and time.time() - started >= cap:
            break
        # Brief drop: wait and resume into the next segment.
        time.sleep(cfg.record.resume_gap_seconds)

    manifest = {
        "room": target.key,
        "url": target.watch_url,
        "files": files,
        "started_at": started,
        "ended_at": time.time(),
    }
    write_json(dest / "manifest.json", manifest)
    write_json(cfg.paths.data / "jobs" / target.key / "record.json", manifest)
    if not files:
        raise MediaError("没有写出任何录像分段")
    return dest


def _record_session(
    cfg: AppConfig,
    status: RoomStatus,
    dest: Path,
    session: int,
    remaining: float | None,
) -> list[Path]:
    before = {p.resolve() for p in dest.glob("*") if p.is_file()}
    stream = status.streams.best()
    prefer = cfg.record.prefer
    errors: list[str] = []

    attempts: list[tuple[str, str]] = []
    if prefer in {"auto", "ytdlp"}:
        attempts.append(("ytdlp-page", status.target.watch_url))
        if stream:
            attempts.append(("ytdlp-stream", stream))
    if prefer in {"auto", "ffmpeg"} and stream:
        attempts.append(("ffmpeg", stream))
    if prefer == "ffmpeg" and not stream:
        attempts.append(("ytdlp-page", status.target.watch_url))

    last_err: Exception | None = None
    for kind, source in attempts:
        log.info("录制尝试 %s session=%s", kind, session)
        try:
            if kind.startswith("ytdlp"):
                _ytdlp_record(cfg, source, dest, session, remaining)
            else:
                _ffmpeg_record(cfg, source, dest, session, remaining)
            after = [p for p in dest.glob("*") if p.is_file() and p.resolve() not in before]
            after = [p for p in after if p.suffix.lower() in {".ts", ".mp4", ".mkv", ".flv", ".part"}]
            if after:
                return sorted(after)
            errors.append(f"{kind}: 没有新文件")
        except Exception as exc:  # noqa: BLE001 — we try the next backend
            last_err = exc
            errors.append(f"{kind}: {exc}")
            log.warning("%s", errors[-1])

    detail = " | ".join(errors) or str(last_err)
    raise MediaError(
        "录制失败。yt-dlp 目前没有 Douyin *直播* extractor（只支持 www.douyin.com/video/ID）。"
        "本工具改为解析直播页里的 HLS/FLV 再交给 yt-dlp/ffmpeg。"
        f" 如果仍然失败，多半需要 cookies / 大陆网络 / 代理。详情: {detail}"
    )


def _ytdlp_record(
    cfg: AppConfig,
    source: str,
    dest: Path,
    session: int,
    remaining: float | None,
) -> None:
    ytdlp = require_ytdlp()
    out_tmpl = str(dest / f"s{session:03d}_%(epoch)s.%(ext)s")
    args = [
        ytdlp,
        "--no-playlist",
        "--no-warnings",
        "--hls-use-mpegts",
        "--retries",
        "3",
        "--fragment-retries",
        "10",
        "-o",
        out_tmpl,
        "--add-header",
        "Referer: https://live.douyin.com/",
        "--add-header",
        f"User-Agent: {cfg.http.user_agent}",
    ]
    if cfg.paths.cookies.is_file():
        args.extend(["--cookies", str(cfg.paths.cookies)])
    if remaining:
        args.extend(["--downloader", "ffmpeg", "--downloader-args", f"ffmpeg:-t {int(remaining)}"])
    args.append(source)
    timeout = (remaining + 30) if remaining else None
    log.debug("yt-dlp %s", source[:80])
    try:
        proc = subprocess.run(args, timeout=timeout, check=False, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        log.info("yt-dlp 达到时长上限，视为本段结束")
        return
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[-1500:]
        raise MediaError(err or f"yt-dlp exit {proc.returncode}")


def _ffmpeg_record(
    cfg: AppConfig,
    source: str,
    dest: Path,
    session: int,
    remaining: float | None,
) -> None:
    ffmpeg = require_ffmpeg()
    pattern = str(dest / f"s{session:03d}_%03d.ts")
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-user_agent",
        cfg.http.user_agent,
        "-headers",
        "Referer: https://live.douyin.com/\r\n",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "8",
        "-i",
        source,
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(cfg.record.segment_seconds),
        "-reset_timestamps",
        "1",
        pattern,
    ]
    if remaining:
        # -t must be before output; insert after input.
        idx = args.index("-c")
        args[idx:idx] = ["-t", str(int(remaining))]
    timeout = (remaining + 30) if remaining else None
    try:
        proc = subprocess.run(args, timeout=timeout, check=False, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        log.info("ffmpeg 达到时长上限，视为本段结束")
        return
    if proc.returncode not in {0, 255, 1}:  # 1/255 often means the live ended
        raise MediaError((proc.stderr or "")[-1500:] or f"ffmpeg exit {proc.returncode}")
    if proc.returncode != 0:
        log.info("ffmpeg 退出 %s（直播常见于流结束）", proc.returncode)


def concat_recordings(files: list[Path], dest: Path) -> Path:
    """Concat segments for detection. Copy-concat when possible."""
    if not files:
        raise MediaError("没有录像文件可拼接")
    if len(files) == 1:
        return files[0]
    require_ffmpeg()
    listing = dest.with_suffix(".concat.txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in files), encoding="utf-8")
    out = dest
    args = [
        shutil.which("ffmpeg") or "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-c",
        "copy",
        "-y",
        str(out),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode != 0:
        raise MediaError(f"拼接失败: {proc.stderr[-1000:]}")
    return out
