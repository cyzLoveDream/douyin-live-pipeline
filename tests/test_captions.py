from dylive.captions import build_ass, write_ass, write_srt
from dylive.transcribe import Word

from tests.conftest import dummy_transcript


def test_ass_contains_dialogue_and_spoken_words():
    words = dummy_transcript(2.0).words
    ass = build_ass(words, style="douyin", width=1080, height=1920)
    assert "Dialogue:" in ass
    assert "家人们" in ass
    assert "买它" in ass


def test_hormozi_and_standard_styles():
    words = [Word(0.0, 0.4, "卧槽", 1.0), Word(0.4, 0.8, "太强", 1.0)]
    h = build_ass(words, style="hormozi")
    s = build_ass(words, style="standard")
    assert "Dialogue:" in h and "卧槽" in h
    assert "Dialogue:" in s and "太强" in s


def test_write_ass_and_srt(tmp_path):
    words = dummy_transcript(2.0).words
    ass_p = write_ass(tmp_path / "c.ass", words, style="douyin")
    srt_p = write_srt(tmp_path / "c.srt", words)
    assert "Dialogue:" in ass_p.read_text(encoding="utf-8")
    srt = srt_p.read_text(encoding="utf-8")
    assert "-->" in srt
    assert "家人们" in srt
