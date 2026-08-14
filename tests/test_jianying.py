from pathlib import Path

from dylive.detect import Highlight
from dylive.edit import render_clip
from dylive.jianying import export_jianying
from dylive.timeline import build_clip_timeline

from tests.conftest import dummy_transcript, make_landscape_video


def test_jianying_export_layout(app_cfg, tmp_path: Path):
    src = make_landscape_video(tmp_path / "rec.mp4", seconds=2.0)
    dest = tmp_path / "clip.mp4"
    app_cfg.edit.style = "clean"
    app_cfg.edit.width = 360
    app_cfg.edit.height = 640
    app_cfg.paths.output = tmp_path / "output" / "clips"
    app_cfg.paths.output.mkdir(parents=True)
    tr = dummy_transcript(2.0)
    h = Highlight(start=0.0, end=1.5, reasons=["t"])
    render_clip(app_cfg, src, h, dest, title="高能", room_id="room9", transcript=tr)
    tl = build_clip_timeline(app_cfg, src, h, tr, room_id="room9", title="高能")
    out = export_jianying(app_cfg, "room9", [dest], words=tr.words, timeline=tl, caption_style="douyin")
    assert (out / "IMPORT.md").is_file()
    assert "剪映" in (out / "IMPORT.md").read_text(encoding="utf-8")
    assert (out / "captions.srt").is_file()
    assert (out / "captions.ass").is_file()
    assert (out / "timeline.json").is_file()
    assert list(out.glob("clip_*.mp4"))
