from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from dylive.config import AppConfig, DetectConfig, EditConfig, PathsConfig


def pytest_configure():
    if not shutil.which("ffmpeg"):
        pytest.exit("ffmpeg is required for tests (apt install ffmpeg / brew install ffmpeg)")


@pytest.fixture
def app_cfg(tmp_path: Path) -> AppConfig:
    cfg = AppConfig(
        paths=PathsConfig(
            recordings=tmp_path / "recordings",
            output=tmp_path / "output",
            data=tmp_path / "data",
            cookies=tmp_path / "cookies.txt",
            browser_profile=tmp_path / "profile",
        ),
        detect=DetectConfig(
            min_clip_seconds=1.0,
            max_clip_seconds=4.0,
            merge_gap_seconds=1.0,
            pad_before_seconds=0.4,
            pad_after_seconds=0.4,
            audio_window_seconds=0.2,
            audio_percentile=80,
            scene_threshold=0.25,
        ),
        edit=EditConfig(
            fill="crop",
            width=360,
            height=640,
            title_card=False,
            source_caption=False,
            whisper=False,
        ),
    )
    cfg.paths.ensure()
    cfg.validate()
    return cfg


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


def make_beep_video(path: Path, *, total: float = 6.0, beep_at: float = 2.0, beep_for: float = 1.0) -> Path:
    """Silence with a loud sine spike, black frames."""
    tail = max(0.2, total - beep_at - beep_for)
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={total}:r=25",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={beep_at}",
            "-f", "lavfi", "-i", f"sine=frequency=1000:sample_rate=44100:duration={beep_for}",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={tail}",
            "-filter_complex", "[1:a][2:a][3:a]concat=n=3:v=0:a=1[a]",
            "-map", "0:v", "-map", "[a]",
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-y", str(path),
        ]
    )
    return path


def make_scene_cut_video(path: Path) -> Path:
    """2s red then 2s blue — a hard scene cut at t=2."""
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=2:r=25",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=2:r=25",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo:d=4",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(path),
        ]
    )
    return path


def make_landscape_video(path: Path, seconds: float = 2.0) -> Path:
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=green:s=640x360:d={seconds}:r=25",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(path),
        ]
    )
    return path


def dummy_transcript(duration: float = 2.0):
    """Word-level fixture so tests never download a whisper model."""
    from dylive.transcribe import Segment, Transcript, Word

    words = [
        Word(0.00, 0.35, "家人们", 0.99),
        Word(0.35, 0.70, "今晚", 0.99),
        Word(0.70, 1.05, "太强", 0.99),
        Word(1.05, 1.40, "了", 0.99),
        Word(1.40, 1.80, "买它", 0.99),
    ]
    if duration > 2.0:
        t = 1.8
        extra = ["真的", "绝了", "秒杀"]
        for tok in extra:
            if t >= duration:
                break
            words.append(Word(t, min(duration, t + 0.35), tok, 0.9))
            t += 0.35
    return Transcript(
        language="zh",
        segments=[Segment(start=words[0].start, end=words[-1].end, text="".join(w.word for w in words), words=words)],
    )


class FakeTranscriber:
    def __init__(self, transcript=None):
        self.transcript = transcript or dummy_transcript()
        self.calls = []

    def transcribe(self, audio_path, *, language="zh"):
        self.calls.append((str(audio_path), language))
        self.transcript.language = language
        return self.transcript
