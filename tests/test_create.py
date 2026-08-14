"""二次创作引擎单测：不联网、不下载模型、不装 edge-tts。"""

from dylive.create import (
    FILLER_WORDS,
    filter_filler_words,
    filler_intervals,
    generate_cta,
    generate_hook,
    generate_narration,
    rewrite_script,
)
from dylive.transcribe import Word


def _words(texts, step=0.2):
    out = []
    t = 0.0
    for tok in texts:
        out.append(Word(t, t + step, tok, 1.0))
        t += step
    return out


def test_filter_filler_words_removes_fillers():
    words = _words(["家人们", "那个", "就是", "绝了", "嗯", "买它"])
    cleaned = filter_filler_words(words)
    assert all((w.word or "").strip() not in FILLER_WORDS for w in cleaned)
    assert [w.word for w in cleaned] == ["家人们", "绝了", "买它"]


def test_filler_intervals():
    words = _words(["那个", "买它", "嗯"])
    intervals = filler_intervals(words)
    assert len(intervals) == 2
    assert intervals[0][0] == words[0].start


def test_rewrite_script_heuristic_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DYLIVE_LLM_API_KEY", raising=False)
    out = rewrite_script("家人们这个真的绝了赶紧冲", room_id="room")
    assert out and len(out) <= 24


def test_generate_hook_heuristic(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DYLIVE_LLM_API_KEY", raising=False)
    out = generate_hook("这段太炸了", index=0)
    assert out and len(out) <= 20


def test_generate_cta_heuristic(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DYLIVE_LLM_API_KEY", raising=False)
    out = generate_cta()
    assert out and len(out) <= 20


def test_generate_narration_heuristic(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DYLIVE_LLM_API_KEY", raising=False)
    out = generate_narration("绝了", title="高能", room_id="room")
    assert out and len(out) <= 24
