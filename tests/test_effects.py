import subprocess
from pathlib import Path

from dylive.detect import Highlight
from dylive.edit import render_clip
from dylive.effects import EFFECT_NAMES, render_effect, xfade_concat
from dylive.media import duration_seconds, video_size

from tests.conftest import dummy_transcript, make_landscape_video, run_ffmpeg


def test_named_effects_have_filters():
    for name in EFFECT_NAMES:
        params = {"start": 0.4, "end": 0.9, "direction": "left", "type": "in"}
        snippet = render_effect(name, "vin", "vout", params, width=360, height=640, duration=2.0)
        assert snippet, name
        assert "[vin]" in snippet and "[vout]" in snippet


def test_douyin_hot_renders_video_audio(app_cfg, tmp_path: Path):
    src = make_landscape_video(tmp_path / "in.mp4", seconds=2.0)
    dest = tmp_path / "hot.mp4"
    app_cfg.edit.style = "douyin_hot"
    app_cfg.edit.caption_style = "douyin"
    app_cfg.edit.width = 360
    app_cfg.edit.height = 640
    app_cfg.edit.source_caption = True
    render_clip(
        app_cfg,
        src,
        Highlight(start=0.0, end=2.0, reasons=["test"]),
        dest,
        title="家人们",
        room_id="room1",
        transcript=dummy_transcript(2.0),
    )
    assert dest.is_file()
    w, h = video_size(dest)
    assert (w, h) == (360, 640)
    assert duration_seconds(dest) >= 1.5
    ass = dest.with_suffix(".ass").read_text(encoding="utf-8")
    assert "Dialogue:" in ass
    assert "家人们" in ass


def test_zoom_in_graph_accepted(tmp_path: Path):
    src = make_landscape_video(tmp_path / "in.mp4", seconds=2.0)
    dest = tmp_path / "z.mp4"
    snippet = render_effect("zoom_in", "0:v", "vout", {"amount": 0.12}, width=320, height=240, duration=2.0)
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-filter_complex", snippet,
            "-map", "[vout]",
            "-an", "-t", "0.4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-y", str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert dest.is_file()


def test_xfade_two_clips(tmp_path: Path):
    a = make_landscape_video(tmp_path / "a.mp4", seconds=1.2)
    b = make_landscape_video(tmp_path / "b.mp4", seconds=1.2)
    dest = tmp_path / "pack.mp4"
    xfade_concat([a, b], dest, xfade=0.25)
    assert dest.is_file()
    assert duration_seconds(dest) >= 1.5
