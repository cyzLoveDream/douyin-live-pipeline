from dylive.detect import Highlight
from dylive.polish import polish_highlights

from tests.conftest import dummy_transcript


def test_heuristic_title_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DYLIVE_LLM_API_KEY", raising=False)
    h = Highlight(start=0.0, end=2.0, reasons=["keywords"], score=3.0)
    out = polish_highlights([h], dummy_transcript(2.0), room_id="room")
    assert out[0].title
    assert out[0].hashtags
    assert any(t.startswith("#") for t in out[0].hashtags)
    assert out[0].hook
    assert "买它" in "".join(w.word for w in dummy_transcript(2.0).words)
