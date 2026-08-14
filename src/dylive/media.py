"""ffmpeg / ffprobe wrappers. ffmpeg is a system dependency."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from dylive.exceptions import MediaError

log = logging.getLogger("dylive.media")

DEFAULT_TIMEOUT = 120


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise MediaError(
            "未找到 ffmpeg。请安装后重试：\n"
            "  Debian/Ubuntu: sudo apt install ffmpeg\n"
            "  macOS: brew install ffmpeg\n"
            "  Windows: winget install ffmpeg 或从 https://ffmpeg.org 下载"
        )
    return path


def require_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise MediaError("未找到 ffprobe（通常与 ffmpeg 一起安装）")
    return path


def require_ytdlp() -> str:
    path = shutil.which("yt-dlp")
    if path:
        return path
    # installed as a module next to us
    return "yt-dlp"


def run(
    args: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    check: bool = True,
    capture: bool = True,
    stdin: bytes | None = None,
) -> subprocess.CompletedProcess[str]:
    log.debug("exec: %s", " ".join(args[:12]) + (" ..." if len(args) > 12 else ""))
    try:
        proc = subprocess.run(
            args,
            input=stdin.decode("utf-8") if isinstance(stdin, bytes) else stdin,
            capture_output=capture,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"命令超时 ({timeout}s): {args[0]}") from exc
    except FileNotFoundError as exc:
        raise MediaError(f"找不到命令: {args[0]}") from exc
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[-2000:]
        raise MediaError(f"{args[0]} 失败 (exit {proc.returncode}): {err}")
    return proc


def ffprobe_json(path: Path) -> dict:
    probe = require_ffprobe()
    proc = run(
        [
            probe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=30,
    )
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe 输出不是 JSON: {path}") from exc


def duration_seconds(path: Path) -> float:
    info = ffprobe_json(path)
    fmt = info.get("format") or {}
    try:
        return float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def video_size(path: Path) -> tuple[int, int]:
    info = ffprobe_json(path)
    for stream in info.get("streams") or []:
        if stream.get("codec_type") == "video":
            return int(stream.get("width") or 0), int(stream.get("height") or 0)
    return 0, 0


def extract_pcm_s16le(path: Path, *, sample_rate: int = 8000) -> bytes:
    """Mono s16le PCM for energy analysis. Small and dependency-free."""
    ffmpeg = require_ffmpeg()
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            timeout=max(60, duration_seconds(path) + 30),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"提取音频超时: {path}") from exc
    if proc.returncode != 0:
        raise MediaError(f"提取音频失败: {(proc.stderr or b'')[-500:]!r}")
    return proc.stdout


def list_media(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    exts = {".mp4", ".ts", ".mkv", ".flv", ".m4a", ".wav", ".webm", ".mpegts"}
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return sorted(files, key=lambda p: p.stat().st_mtime)


CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def find_font() -> str | None:
    for p in CJK_FONT_CANDIDATES:
        if Path(p).is_file():
            return p
    return None
