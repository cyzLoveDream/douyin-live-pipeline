"""CLI: dylive run|watch|record|transcribe|detect|edit|compile|publish|login."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer

from dylive import __version__
from dylive.config import AppConfig, load_config
from dylive.exceptions import DyliveError
from dylive.logutil import setup_logging

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="抖音直播高能切片流水线：watch → record → transcribe → detect → edit → compile → publish",
)

log = logging.getLogger("dylive.cli")


def _cfg(ctx: typer.Context) -> AppConfig:
    return ctx.obj


@app.callback()
def main(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="YAML 配置文件（默认 ./config.yaml）"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="调试日志"),
) -> None:
    setup_logging(verbose)
    ctx.obj = load_config(config)
    ctx.obj.paths.ensure()


@app.command()
def version() -> None:
    """Print package version."""
    typer.echo(__version__)


@app.command()
def watch(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="live.douyin.com 或 v.douyin.com 分享链"),
    once: bool = typer.Option(False, "--once", help="只查一次，未开播则退出"),
) -> None:
    """解析房间并轮询直到开播。"""
    from dylive.watch import wait_until_live

    _run(lambda: wait_until_live(_cfg(ctx), url, once=once))


@app.command()
def record(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="直播间 URL"),
    no_wait: bool = typer.Option(False, "--no-wait", help="未开播则立刻失败"),
    max_seconds: Optional[float] = typer.Option(None, "--max-seconds", help="最长录制秒数"),
) -> None:
    """开播后用 yt-dlp / ffmpeg 分段录像。"""
    from dylive.record import record_url

    dest = _run(lambda: record_url(_cfg(ctx), url, wait=not no_wait, max_seconds=max_seconds))
    if dest:
        typer.echo(str(dest))


@app.command()
def transcribe(
    ctx: typer.Context,
    source: Optional[str] = typer.Argument(
        None, help="录像文件 / 目录 / 房间 id；默认最近一次 job"
    ),
) -> None:
    """转写口播（faster-whisper，词级时间戳）。检测和烧录字幕都依赖这一步。"""
    from dylive.transcribe import transcribe_job

    def go():
        media, tr = transcribe_job(_cfg(ctx), source)
        typer.echo(f"media: {media}")
        typer.echo(f"segments: {len(tr.segments)}  words: {len(tr.words)}  lang: {tr.language}")
        return tr

    _run(go)


@app.command()
def detect(
    ctx: typer.Context,
    source: Optional[str] = typer.Argument(
        None, help="录像文件 / 目录 / 房间 id；默认最近一次 job"
    ),
) -> None:
    """检测高能场面（RMS z-score + 频谱通量 + 口播/VAD + 切镜 + 关键词）。缺转写会先 transcribe。"""
    from dylive.detect import detect_job

    def go():
        media, highs = detect_job(_cfg(ctx), source)
        typer.echo(f"media: {media}")
        for i, h in enumerate(highs, 1):
            why = " ".join(f"{k}={v:.2f}" for k, v in (h.why or {}).items() if v)
            typer.echo(f"  {i:02d}  {h.start:.1f}-{h.end:.1f}s  score={h.score:.2f}  {why}")
        if not highs:
            typer.echo("没有检测到高能片段（可调 detect.weights / keywords / max_clips）")
        return highs

    _run(go)


@app.command()
def edit(
    ctx: typer.Context,
    source: Optional[str] = typer.Argument(
        None, help="highlights.json / 房间 id / 录像；默认最近一次检测结果"
    ),
    title: Optional[str] = typer.Option(None, "--title", help="标题卡 / hook 文字"),
    room_id: Optional[str] = typer.Option(None, "--room-id", help="来源字幕里的房间 id"),
) -> None:
    """二次剪辑：9:16、特效预设、强制烧录词级字幕。没有转写会先 transcribe。"""
    from dylive.edit import edit_job

    clips = _run(lambda: edit_job(_cfg(ctx), source, title=title, room_id=room_id))
    if clips:
        for c in clips:
            typer.echo(str(c))


@app.command("compile")
def compile_cmd(
    ctx: typer.Context,
    source: Optional[str] = typer.Argument(
        None, help="房间 id / edit.json 所在目录；默认最近一次 edit"
    ),
) -> None:
    """把成片 xfade 合成 output/clips/<room>_pack.mp4。"""
    from dylive.edit import compile_job

    dest = _run(lambda: compile_job(_cfg(ctx), source))
    if dest:
        typer.echo(str(dest))


@app.command()
def publish(
    ctx: typer.Context,
    clips: Optional[list[Path]] = typer.Argument(None, help="成片 mp4，默认 output/clips"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只打印，不打开浏览器"),
    title: Optional[str] = typer.Option(None, "--title"),
    description: Optional[str] = typer.Option(None, "--description", "--desc"),
) -> None:
    """用 Playwright 打开创作者中心上传页。验证码/扫码会暂停等你。"""
    from dylive.publish import publish_clips

    paths = list(clips) if clips else None
    results = _run(
        lambda: publish_clips(_cfg(ctx), paths, dry_run=dry_run, title=title, description=description)
    )
    if results:
        for row in results:
            typer.echo(f"{row.get('status')}: {row.get('clip')}")


@app.command()
def login(
    ctx: typer.Context,
    no_export: bool = typer.Option(False, "--no-export", help="不写出 cookies.txt"),
    timeout: float = typer.Option(300, "--timeout", help="等待扫码秒数"),
) -> None:
    """打开持久化浏览器，用抖音 App 扫码登录创作者中心。"""
    from dylive.login import login as do_login

    _run(lambda: do_login(_cfg(ctx), export_cookies=not no_export, timeout=timeout))


@app.command()
def run(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="直播间 URL"),
    dry_run: bool = typer.Option(False, "--dry-run", help="剪辑后不发布"),
    max_seconds: Optional[float] = typer.Option(None, "--max-seconds", help="最长录制秒数"),
    title: Optional[str] = typer.Option(None, "--title"),
) -> None:
    """全流程: watch → record → transcribe → detect → edit → compile → publish。"""
    from dylive.detect import detect_job
    from dylive.edit import compile_job, edit_job
    from dylive.publish import publish_clips
    from dylive.record import record_url
    from dylive.transcribe import transcribe_job

    cfg = _cfg(ctx)

    def go():
        dest = record_url(cfg, url, wait=True, max_seconds=max_seconds)
        typer.echo(f"recordings: {dest}")
        media, tr = transcribe_job(cfg, dest)
        typer.echo(f"transcript: {len(tr.words)} words  media={media}")
        media, highs = detect_job(cfg, dest)
        typer.echo(f"highlights: {len(highs)}  media={media}")
        clips = edit_job(cfg, dest.name, title=title, room_id=dest.name)
        for c in clips:
            typer.echo(f"clip: {c}")
        try:
            pack = compile_job(cfg, dest.name)
            typer.echo(f"pack: {pack}")
        except Exception as exc:  # noqa: BLE001
            log.warning("合集跳过: %s", exc)
        pub_title = title or (highs[0].title if highs else None)
        publish_clips(cfg, clips, dry_run=dry_run, title=pub_title)
        return clips

    _run(go)


def _run(fn):
    try:
        return fn()
    except DyliveError as exc:
        log.error("%s", exc)
        raise typer.Exit(code=1) from exc
