"""智能导演单测：不联网、不调 LLM，验证启发式降级与决策合并。"""

from dylive.director import (
    EFFECT_KEYS,
    STYLES,
    _heuristic,
    _merge,
    direct_clip,
)


def _fake_features(energy=0.8, flux=0.7, speech=0.9, kw=True):
    return {
        "duration": 30.0,
        "score": 0.9,
        "signals": {
            "energy": energy,
            "flux": flux,
            "speech": speech,
            "scene": 0.3,
            "keywords": 0.8,
            "chat": 0.5,
        },
        "keywords_hit": ["绝了"] if kw else [],
        "chat_events": 5,
        "text": "家人们这个真的绝了",
    }


def test_heuristic_high_energy():
    d = _heuristic(_fake_features(), "关注主播", 0)
    assert d["style"] in STYLES
    assert d["effects"]["punch"] is True
    assert d["title"]
    assert d["hook"]
    assert d["cta"] == "关注主播"
    assert set(d["effects"].keys()) == set(EFFECT_KEYS)


def test_heuristic_cinematic_when_low_speech():
    d = _heuristic(_fake_features(speech=0.1), "关注主播", 0)
    assert d["style"] == "cinematic"
    assert d["effects"]["vignette"] is True


def test_merge_prefers_valid_llm_fields():
    fb = _heuristic(_fake_features(), "关注主播", 0)
    llm = {
        "style": "cinematic",
        "effects": {"punch": False, "vignette": True, "grain": True},
        "xfade": "fade",
        "caption_style": "standard",
        "title": "一段故事",
        "hook": "注意看",
        "hashtags": ["#故事", "#直播"],
        "description": "这条值得看完",
        "reason": "情绪细腻",
    }
    m = _merge(llm, fb)
    assert m["style"] == "cinematic"
    assert m["effects"]["punch"] is False
    assert m["effects"]["vignette"] is True
    assert m["caption_style"] == "standard"
    assert m["title"] == "一段故事"


def test_merge_rejects_invalid_style():
    fb = _heuristic(_fake_features(), "关注主播", 0)
    m = _merge({"style": "not_a_style", "effects": {}, "title": "x"}, fb)
    assert m["style"] == fb["style"]


def test_direct_clip_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("DYLIVE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    d = direct_clip(_fake_features(), cta="关注主播", index=0)
    assert d["style"] in STYLES
    assert set(d["effects"].keys()) == set(EFFECT_KEYS)
