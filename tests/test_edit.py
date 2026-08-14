from pathlib import Path

from dylive.detect import Highlight
from dylive.edit import render_clip
from dylive.media import duration_seconds, video_size

from tests.conftest import dummy_transcript, make_landscape_video


def test_crop_to_9_16(app_cfg, tmp_path: Path):
    src = make_landscape_video(tmp_path / "wide.mp4", seconds=2.0)
    dest = tmp_path / "out.mp4"
    h = Highlight(start=0.0, end=1.5, reasons=["test"])
    app_cfg.edit.style = "clean"
    app_cfg.edit.fill = "crop"
    app_cfg.edit.title_card = False
    app_cfg.edit.source_caption = True
    render_clip(
        app_cfg,
        src,
        h,
        dest,
        title="测试标题",
        room_id="12345",
        transcript=dummy_transcript(2.0),
    )
    assert dest.is_file()
    assert dest.with_suffix(".ass").is_file()
    w, hgt = video_size(dest)
    assert w == app_cfg.edit.width
    assert hgt == app_cfg.edit.height
    assert duration_seconds(dest) >= 1.0
    ass = dest.with_suffix(".ass").read_text(encoding="utf-8")
    assert "Dialogue:" in ass
    assert "家人们" in ass


def test_blur_fill_douyin_hot(app_cfg, tmp_path: Path):
    src = make_landscape_video(tmp_path / "wide.mp4", seconds=2.0)
    dest = tmp_path / "out.mp4"
    app_cfg.edit.style = "douyin_hot"
    app_cfg.edit.caption_style = "douyin"
    app_cfg.edit.source_caption = True
    h = Highlight(start=0.0, end=1.2, reasons=["test"])
    render_clip(
        app_cfg,
        src,
        h,
        dest,
        title="高能",
        room_id="room9",
        transcript=dummy_transcript(2.0),
    )
    assert dest.is_file()
    w, hgt = video_size(dest)
    assert (w, hgt) == (app_cfg.edit.width, app_cfg.edit.height)
    assert duration_seconds(dest) >= 1.0
    assert dest.with_suffix(".ass").is_file()
