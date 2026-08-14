from pathlib import Path

from dylive.detect import Highlight
from dylive.edit import render_clip
from dylive.media import duration_seconds, video_size

from tests.conftest import dummy_transcript, make_landscape_video, run_ffmpeg


def _quiet_sine(path: Path, seconds: float = 2.0) -> Path:
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}:sample_rate=44100",
            "-filter:a", "volume=0.08",
            "-y", str(path),
        ]
    )
    return path


def test_bgm_mix_or_skip(app_cfg, tmp_path: Path):
    src = make_landscape_video(tmp_path / "in.mp4", seconds=2.0)
    bgm = _quiet_sine(tmp_path / "bgm.wav", 2.0)
    dest = tmp_path / "out.mp4"
    app_cfg.edit.style = "clean"
    app_cfg.edit.width = 360
    app_cfg.edit.height = 640
    app_cfg.edit.bgm = bgm
    render_clip(
        app_cfg, src, Highlight(start=0.0, end=1.5, reasons=["t"]), dest,
        title="t", room_id="r", transcript=dummy_transcript(2.0),
    )
    assert dest.is_file()
    assert video_size(dest) == (360, 640)
    assert duration_seconds(dest) >= 1.0
