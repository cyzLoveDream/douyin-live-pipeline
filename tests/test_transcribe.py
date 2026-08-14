from pathlib import Path

from dylive.transcribe import load_transcript, save_transcript, transcribe_job

from tests.conftest import FakeTranscriber, dummy_transcript, make_landscape_video


def test_transcribe_writes_json_with_stub(app_cfg, tmp_path: Path):
    media = make_landscape_video(tmp_path / "talk.mp4", seconds=2.0)
    fake = FakeTranscriber(dummy_transcript(2.0))
    out_media, tr = transcribe_job(app_cfg, media, transcriber=fake)
    assert out_media == media
    assert tr.words
    dest = app_cfg.paths.data / "jobs" / tmp_path.name / "transcript.json"
    assert dest.is_file()
    loaded = load_transcript(dest)
    assert loaded.words[0].word == "家人们"
    assert fake.calls
    assert loaded.words[0].prob >= 0.9


def test_save_load_roundtrip(tmp_path: Path):
    tr = dummy_transcript(2.0)
    tr.media = "x.mp4"
    path = tmp_path / "transcript.json"
    save_transcript(path, tr)
    loaded = load_transcript(path)
    assert [w.word for w in loaded.words] == [w.word for w in tr.words]
