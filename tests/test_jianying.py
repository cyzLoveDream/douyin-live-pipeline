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


def test_missing_library_message():
    from dylive.jianying import missing_library_error

    msg = str(missing_library_error())
    assert "dylive[jianying]" in msg
    assert "官方" in msg


def test_build_segment_list_and_fake_writer(app_cfg, tmp_path: Path):
    from dylive.jianying import DraftClip, build_draft_segments, write_jianying_draft
    from dylive.state import write_json

    src = make_landscape_video(tmp_path / "rec.mp4", seconds=2.0)
    dest = tmp_path / "clip.mp4"
    app_cfg.edit.style = "douyin_hot"
    app_cfg.edit.width = 360
    app_cfg.edit.height = 640
    tr = dummy_transcript(2.0)
    h = Highlight(start=0.0, end=1.5, reasons=["t"], score=1.2)
    render_clip(app_cfg, src, h, dest, title="高能", room_id="room9", transcript=tr)
    job = app_cfg.paths.data / "jobs" / "room9"
    job.mkdir(parents=True, exist_ok=True)
    write_json(job / "edit.json", {"clips": [str(dest)], "media": str(src)})
    write_json(
        job / "highlights.json",
        {"media": str(src), "highlights": [{"start": 0.0, "end": 1.5, "score": 1.2, "title": "高能"}]},
    )
    segs = build_draft_segments(app_cfg, "room9")
    assert segs
    assert isinstance(segs[0], DraftClip)
    assert segs[0].media.exists() or Path(segs[0].media).name.endswith(".mp4")
    assert segs[0].filter_name
    assert segs[0].intro_name
    assert segs[0].transition

    class FakeWriter:
        def __init__(self):
            self.clips = None

        def write(self, dest_dir, clips, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / "draft_content.json").write_text("{\"id\": \"fake\"}", encoding="utf-8")
            (dest_dir / "assets").mkdir(exist_ok=True)
            self.clips = list(clips)
            self.kwargs = kwargs
            return dest_dir

    fake = FakeWriter()
    out = write_jianying_draft(app_cfg, "room9", writer=fake)
    assert (out / "draft_content.json").is_file()
    assert fake.clips and len(fake.clips) >= 1
    assert fake.kwargs.get("width") == 360
