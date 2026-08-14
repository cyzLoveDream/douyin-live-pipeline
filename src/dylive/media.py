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
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/WenQuanYiMicroHei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttf",
    "C:/Windows/Fonts/simhei.ttf",
]

_LATIN_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

_FONT_INSTALL_HINT = (
    "找不到中文字体，烧录字幕需要 Noto Sans CJK / Noto Sans SC / 文泉驿 / PingFang。\n"
    "  Debian/Ubuntu: sudo apt install fonts-noto-cjk\n"
    "  macOS: 系统自带 PingFang\n"
    "  Windows: 微软雅黑 msyh.ttc"
)


def find_cjk_font() -> str | None:
    for p in CJK_FONT_CANDIDATES:
        if Path(p).is_file():
            return p
    found = _fc_list_cjk()
    if found:
        return found
    return None


def find_font() -> str | None:
    """CJK font if possible, otherwise a Latin fallback for non-caption drawtext."""
    cjk = find_cjk_font()
    if cjk:
        return cjk
    if Path(_LATIN_FALLBACK).is_file():
        return _LATIN_FALLBACK
    return None


def require_cjk_font() -> str:
    path = find_cjk_font()
    if not path:
        raise MediaError(_FONT_INSTALL_HINT)
    return path


def font_family_name(path: str) -> str:
    key = path.replace("-", "").replace("_", "").replace(" ", "").lower()
    if "notosanscjk" in key or "notosanscjksc" in key:
        return "Noto Sans CJK SC"
    if "notosanssc" in key:
        return "Noto Sans SC"
    if "wqy" in key or "wenquanyi" in key or "microhei" in key or "zenhei" in key:
        return "WenQuanYi Micro Hei"
    if "pingfang" in key:
        return "PingFang SC"
    if "msyh" in key:
        return "Microsoft YaHei"
    if "stheiti" in key or "heiti" in key:
        return "Heiti SC"
    if "simhei" in key:
        return "SimHei"
    return "Noto Sans CJK SC"


def _fc_list_cjk() -> str | None:
    fc = shutil.which("fc-list")
    if not fc:
        return None
    try:
        proc = subprocess.run(
            [fc, ":lang=zh", "file"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in (proc.stdout or "").splitlines():
        path = line.split(":")[0].strip()
        if path and Path(path).is_file():
            return path
    return None
