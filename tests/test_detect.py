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


def test_loud_burst_is_top_window():
    import numpy as np

    from dylive.config import DetectConfig
    from dylive.detect import analyze_samples, score_and_select

    sr = 8000
    total = 10.0
    samples = np.zeros(int(sr * total), dtype=np.float32)
    t0, t1 = int(4.0 * sr), int(5.0 * sr)
    samples[t0:t1] = 0.7 * np.sin(2 * np.pi * 440 * np.arange(t1 - t0) / sr)
    frames = analyze_samples(samples, sr, 0.25)
    cfg = DetectConfig(
        min_clip_seconds=2,
        max_clip_seconds=5,
        max_clips=1,
        pad_before_seconds=0.4,
        pad_after_seconds=0.4,
        merge_gap_seconds=1,
    )
    highs = score_and_select(frames, total, cfg)
    assert highs, "expected a window covering the loud burst"
    top = highs[0]
    assert top.start <= 4.2
    assert top.end >= 4.8
    assert top.why["energy"] > 0


def test_keyword_boosts_quiet_region():
    import numpy as np

    from dylive.config import DetectConfig
    from dylive.detect import analyze_samples, score_and_select
    from dylive.transcribe import Segment, Transcript, Word

    sr = 8000
    total = 12.0
    samples = np.ones(int(sr * total), dtype=np.float32) * 0.004
    frames = analyze_samples(samples, sr, 0.25)
    tr = Transcript(
        language="zh",
        segments=[
            Segment(
                start=8.0,
                end=8.6,
                text="家人们买它",
                words=[Word(8.0, 8.3, "家人们", 0.9), Word(8.3, 8.6, "买它", 0.95)],
            )
        ],
    )
    cfg = DetectConfig(
        min_clip_seconds=2,
        max_clip_seconds=6,
        max_clips=1,
        pad_before_seconds=0.5,
        pad_after_seconds=0.5,
        keywords=["买它"],
        merge_gap_seconds=1,
    )
    highs = score_and_select(frames, total, cfg, transcript=tr)
    assert highs
    assert highs[0].start <= 8.3 <= highs[0].end
    assert highs[0].why["keywords"] > 0


def test_snap_window_to_word_boundaries():
    from dylive.detect import snap_window
    from dylive.transcribe import Word

    words = [
        Word(1.0, 1.3, "hello", 1.0),
        Word(1.3, 1.6, "world", 1.0),
        Word(5.0, 5.4, "end", 1.0),
    ]
    start, end = snap_window(1.12, 5.22, words, max_delta=0.35)
    assert abs(start - 1.0) < 1e-6
    assert abs(end - 5.4) < 1e-6
