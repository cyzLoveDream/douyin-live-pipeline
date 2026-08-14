from pathlib import Path

from dylive.config import DetectConfig
from dylive.detect import Event, audio_energy_events, merge_windows, scene_cut_events

from tests.conftest import make_beep_video, make_scene_cut_video


def test_merge_nearby_and_clamp():
    cfg = DetectConfig(
        min_clip_seconds=12,
        max_clip_seconds=20,
        merge_gap_seconds=3,
        pad_before_seconds=1,
        pad_after_seconds=1,
    )
    events = [
        Event(t=10, reason="audio"),
        Event(t=12, reason="scene"),
        Event(t=40, reason="gift", weight=2),
    ]
    highs = merge_windows(events, cfg, duration=80)
    assert len(highs) == 2
    first = highs[0]
    assert first.start <= 9
    assert first.end >= 13
    assert first.duration >= 12
    assert first.duration <= 20
    assert "audio" in first.reasons and "scene" in first.reasons
    assert highs[1].duration >= 12
    assert highs[1].duration <= 20


def test_split_overlong():
    cfg = DetectConfig(min_clip_seconds=5, max_clip_seconds=10, merge_gap_seconds=1, pad_before_seconds=0, pad_after_seconds=0)
    events = [Event(t=0, reason="audio", span=(0, 25))]
    highs = merge_windows(events, cfg, duration=25)
    assert len(highs) >= 2
    assert all(h.duration <= 10.05 for h in highs)


def test_audio_energy_finds_beep(tmp_path: Path):
    media = make_beep_video(tmp_path / "beep.mp4")
    cfg = DetectConfig(
        audio_window_seconds=0.2,
        audio_percentile=85,
        pad_before_seconds=0.3,
        pad_after_seconds=0.3,
        min_clip_seconds=1,
        max_clip_seconds=4,
    )
    events = audio_energy_events(media, cfg, duration=6.0)
    assert events, "expected an audio spike around t=2s"
    mid = events[0].t if events[0].span is None else sum(events[0].span) / 2
    assert 1.2 <= mid <= 3.5


def test_scene_cut_finds_color_change(tmp_path: Path):
    media = make_scene_cut_video(tmp_path / "cut.mp4")
    cfg = DetectConfig(scene_threshold=0.3)
    events = scene_cut_events(media, cfg)
    assert events, "expected a scene cut near t=2s"
    times = [e.t for e in events]
    assert any(1.5 <= t <= 2.5 for t in times)


def test_full_detect_on_beep(app_cfg, tmp_path: Path):
    from dylive.detect import detect_media

    media = make_beep_video(tmp_path / "beep.mp4")
    highs = detect_media(app_cfg, media)
    assert highs
    assert all(h.duration <= app_cfg.detect.max_clip_seconds + 0.05 for h in highs)
    assert all(h.end > h.start for h in highs)
