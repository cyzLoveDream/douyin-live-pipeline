"""CLI: dylive run|watch|record|transcribe|detect|create|edit|compile|jianying|ui|publish|login."""

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
    help="抖音直播高能切片流水线：watch → record → transcribe → detect → create → edit → compile → 剪映草稿 / 本地 UI → publish",
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
def create(
    ctx: typer.Context,
    source: Optional[str] = typer.Argument(
        None, help="highlights.json / 房间 id；默认最近一次检测结果"
    ),
) -> None:
    """二次创作：文案改写、开场钩子、结尾 CTA、解说稿、剪口播删词；可选 edge-tts 配音。"""
    from dylive.create import create_job

    def go():
        payload = create_job(_cfg(ctx), source)
        typer.echo(f"cta: {payload.get('cta')}")
        for c in payload.get("clips") or []:
            typer.echo(f"  {c['index'] + 1:02d}  {c['title']}  hook={c['hook']}")
            if c.get("voice"):
                typer.echo(f"        voice={c['voice']}")
        return payload

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
    """二次剪辑：9:16、特效预设、强制烧录词级字幕、可选 CTA 花字与配音。没有转写会先 transcribe。"""
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
def jianying(
    ctx: typer.Context,
    room: Optional[str] = typer.Argument(None, help="房间 id；默认最近一次 job"),
) -> None:
    """用 pyJianYingDraft 写出剪映专业版可打开的草稿目录。"""
    from dylive.jianying import OPEN_HINT, write_jianying_draft

    dest = _run(lambda: write_jianying_draft(_cfg(ctx), room))
    if dest:
        typer.echo(str(dest))
        typer.echo(OPEN_HINT)


@app.command()
def ui(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="不自动打开浏览器"),
) -> None:
    """启动本地客户端（FastAPI + 自包含 SPA），默认 http://127.0.0.1:8787 。"""
    from dylive.server import serve_ui

    typer.echo("打开 http://127.0.0.1:8787 查看成片")
    serve_ui(_cfg(ctx), host=host, port=port, open_browser=not no_browser)


@app.command()
def run(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="直播间 URL"),
    dry_run: bool = typer.Option(False, "--dry-run", help="剪辑后不发布"),
    max_seconds: Optional[float] = typer.Option(None, "--max-seconds", help="最长录制秒数"),
    title: Optional[str] = typer.Option(None, "--title"),
    ui: bool = typer.Option(False, "--ui", help="结束后启动本地客户端"),
    open_ui: Optional[bool] = typer.Option(
        None, "--open-ui/--no-open-ui", help="结束后打开浏览器查看成片（有显示器时默认打开）"
    ),
) -> None:
    """全流程: watch → record → transcribe → detect → create → edit → compile → publish。"""
    from dylive.pipeline import run_pipeline
    from dylive.server import has_display, serve_ui

    cfg = _cfg(ctx)

    def go():
        result = run_pipeline(cfg, url, dry_run=dry_run, max_seconds=max_seconds, title=title)
        typer.echo(f"recordings room: {result.get('room')}")
        for c in result.get("clips") or []:
            typer.echo(f"clip: {c}")
        if result.get("pack"):
            typer.echo(f"pack: {result['pack']}")
        typer.echo("打开 http://127.0.0.1:8787 查看成片")
        launch = ui or (open_ui if open_ui is not None else has_display())
        if launch:
            serve_ui(cfg, open_browser=open_ui is not False)
        return result.get("clips")

    _run(go)


def _run(fn):
    try:
        return fn()
    except DyliveError as exc:
        log.error("%s", exc)
        raise typer.Exit(code=1) from exc
