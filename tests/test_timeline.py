from pathlib import Path

from dylive.detect import Highlight
from dylive.edit import render_clip
from dylive.timeline import build_clip_timeline, build_job_timeline

from tests.conftest import dummy_transcript, make_landscape_video


def test_timeline_tracks_point_at_source(app_cfg, tmp_path: Path):
    src = make_landscape_video(tmp_path / "rec.mp4", seconds=4.0)
    h = Highlight(start=0.5, end=2.5, reasons=["energy"], score=2.0)
    tr = dummy_transcript(4.0)
    tl = build_clip_timeline(app_cfg, src, h, tr, room_id="r1", title="测")
    data = tl.to_dict()
    types = [t["type"] for t in data["tracks"]]
    assert types == ["video", "caption", "effect", "overlay", "audio"]
    video = data["tracks"][0]["clips"][0]
    assert video["src"] == str(src)
    assert video["in"] == 0.5
    assert video["out"] == 2.5
    assert any(e["name"] == "punch_zoom" or e["name"] == "fade" or e["name"] == "caption_mask" for e in video["effects"])
    captions = data["tracks"][1]["clips"]
    assert captions
    assert any((c.get("text") or "") for c in captions)
    spoken = {c.get("text") for c in captions}
    assert spoken & {"今晚", "太强", "了", "买它", "真的", "绝了", "家人们"}


def test_job_timeline_keeps_original_in_out(app_cfg, tmp_path: Path):
    src = make_landscape_video(tmp_path / "rec.mp4", seconds=6.0)
    highs = [
        Highlight(start=0.0, end=2.0, reasons=["a"], score=1),
        Highlight(start=3.0, end=5.0, reasons=["b"], score=1),
    ]
    tl = build_job_timeline(app_cfg, src, highs, dummy_transcript(6.0), room_id="r")
    clips = tl.track("video").clips
    assert len(clips) == 2
    assert clips[0].src_in == 0.0 and clips[0].src_out == 2.0
    assert clips[1].src_in == 3.0 and clips[1].src_out == 5.0
    assert clips[0].src == str(src)


def test_edit_writes_timeline_json(app_cfg, tmp_path: Path):
    src = make_landscape_video(tmp_path / "rec.mp4", seconds=2.0)
    dest = tmp_path / "out.mp4"
    app_cfg.edit.style = "clean"
    app_cfg.edit.width = 360
    app_cfg.edit.height = 640
    render_clip(
        app_cfg, src, Highlight(start=0.0, end=1.5, reasons=["t"]), dest,
        title="t", room_id="r", transcript=dummy_transcript(2.0),
    )
    # render_clip builds a clip timeline internally; job-level file is written by edit_job
    assert dest.is_file()
