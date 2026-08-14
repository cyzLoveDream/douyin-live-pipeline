"""First-class transcribe stage: faster-whisper with word timestamps.

Tests inject a Transcriber (or a fixture transcript.json) so models are never downloaded.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from dylive.config import AppConfig
from dylive.exceptions import MediaError
from dylive.media import duration_seconds, require_ffmpeg
from dylive.state import job_dir, read_json, write_json

log = logging.getLogger("dylive.transcribe")


@dataclass
class Word:
    start: float
    end: float
    word: str
    prob: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    media: str = ""
    language: str = "zh"
    segments: list[Segment] = field(default_factory=list)

    @property
    def words(self) -> list[Word]:
        out: list[Word] = []
        for seg in self.segments:
            out.extend(seg.words)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "media": self.media,
            "language": self.language,
            "segments": [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                    "words": [asdict(w) for w in seg.words],
                }
                for seg in self.segments
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        segs: list[Segment] = []
        for raw in data.get("segments") or []:
            words = [
                Word(
                    start=float(w.get("start") or 0),
                    end=float(w.get("end") or 0),
                    word=str(w.get("word") or ""),
                    prob=float(w.get("prob") if w.get("prob") is not None else 1.0),
                )
                for w in (raw.get("words") or [])
            ]
            segs.append(
                Segment(
                    start=float(raw.get("start") or 0),
                    end=float(raw.get("end") or 0),
                    text=str(raw.get("text") or ""),
                    words=words,
                )
            )
        return cls(
            media=str(data.get("media") or ""),
            language=str(data.get("language") or "zh"),
            segments=segs,
        )


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, *, language: str = "zh") -> Transcript: ...


class FasterWhisperTranscriber:
    """Production transcriber. Import is lazy so tests never pull torch/models."""

    def __init__(
        self,
        model: str = "small",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        vad_filter: bool = True,
        word_timestamps: bool = True,
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.vad_filter = vad_filter
        self.word_timestamps = word_timestamps
        self._model: Any = None

    @classmethod
    def from_config(cls, cfg: AppConfig) -> FasterWhisperTranscriber:
        t = cfg.transcribe
        return cls(
            model=t.model,
            device=t.device,
            compute_type=t.compute_type,
            vad_filter=t.vad_filter,
            word_timestamps=t.word_timestamps,
        )

    def transcribe(self, audio_path: Path, *, language: str = "zh") -> Transcript:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise MediaError(
                "faster-whisper 是硬依赖。请安装: pip install faster-whisper"
            ) from exc
        if self._model is None:
            log.info("加载 whisper 模型 %s (%s/%s)", self.model_name, self.device, self.compute_type)
            self._model = WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type
            )
        segments, info = self._model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=self.word_timestamps,
            vad_filter=self.vad_filter,
        )
        segs: list[Segment] = []
        for seg in segments:
            words: list[Word] = []
            for w in getattr(seg, "words", None) or []:
                token = str(getattr(w, "word", "") or "").strip()
                if not token:
                    continue
                words.append(
                    Word(
                        start=float(w.start),
                        end=float(max(w.end, w.start + 0.04)),
                        word=token,
                        prob=float(getattr(w, "probability", 1.0) or 1.0),
                    )
                )
            text = str(getattr(seg, "text", "") or "").strip()
            if not words and text:
                words = [
                    Word(
                        start=float(seg.start),
                        end=float(max(seg.end, seg.start + 0.04)),
                        word=text,
                        prob=1.0,
                    )
                ]
            segs.append(
                Segment(
                    start=float(seg.start),
                    end=float(seg.end),
                    text=text,
                    words=words,
                )
            )
        lang = str(getattr(info, "language", language) or language)
        return Transcript(language=lang, segments=segs)


def transcript_path(cfg: AppConfig, job_key: str) -> Path:
    return job_dir(cfg, job_key) / "transcript.json"


def save_transcript(path: Path, transcript: Transcript) -> None:
    write_json(path, transcript.to_dict())


def load_transcript(path: Path) -> Transcript:
    data = read_json(path)
    if not isinstance(data, dict):
        raise MediaError(f"transcript.json 格式错误: {path}")
    return Transcript.from_dict(data)


def transcribe_job(
    cfg: AppConfig,
    source: str | Path | None = None,
    *,
    transcriber: Transcriber | None = None,
) -> tuple[Path, Transcript]:
    from dylive.detect import resolve_media

    media, job_key = resolve_media(cfg, source)
    tr = transcribe_media(cfg, media, job_key=job_key, transcriber=transcriber)
    return media, tr


def transcribe_media(
    cfg: AppConfig,
    media: Path,
    *,
    job_key: str | None = None,
    transcriber: Transcriber | None = None,
) -> Transcript:
    if not media.is_file():
        raise MediaError(f"找不到媒体文件: {media}")
    engine = transcriber or FasterWhisperTranscriber.from_config(cfg)
    wav = media.with_name(media.stem + "._dylive_asr.wav")
    _extract_wav_16k(media, wav)
    try:
        tr = engine.transcribe(wav, language=cfg.transcribe.language)
    finally:
        wav.unlink(missing_ok=True)
    tr.media = str(media)
    if not tr.language:
        tr.language = cfg.transcribe.language
    n_words = len(tr.words)
    log.info("转写完成 %s  段=%s 词=%s  lang=%s", media.name, len(tr.segments), n_words, tr.language)
    if job_key:
        dest = transcript_path(cfg, job_key)
        save_transcript(dest, tr)
        log.info("写出 %s", dest)
    return tr


def ensure_transcript(
    cfg: AppConfig,
    media: Path,
    job_key: str,
    *,
    transcriber: Transcriber | None = None,
) -> Transcript:
    path = transcript_path(cfg, job_key)
    if path.is_file():
        log.info("复用转写 %s", path)
        tr = load_transcript(path)
        if not tr.media:
            tr.media = str(media)
        return tr
    return transcribe_media(cfg, media, job_key=job_key, transcriber=transcriber)


def _extract_wav_16k(media: Path, dest: Path) -> Path:
    ffmpeg = require_ffmpeg()
    import subprocess

    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(media),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-y",
        str(dest),
    ]
    timeout = max(60, duration_seconds(media) + 30)
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0 or not dest.is_file():
        raise MediaError(f"抽取 16kHz wav 失败: {(proc.stderr or '')[-800:]}")
    return dest
