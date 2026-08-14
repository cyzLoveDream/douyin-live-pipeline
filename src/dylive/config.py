"""Load and validate YAML config plus environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dylive.exceptions import ConfigError

DEFAULT_CONFIG_NAME = "config.yaml"

DEFAULT_KEYWORDS = [
    "卧槽",
    "绝了",
    "太强",
    "牛逼",
    "哈哈",
    "买它",
    "秒杀",
    "免费",
    "倒计时",
    "送给",
    "家人们",
]


def _as_path(value: Any, default: str) -> Path:
    raw = default if value is None else str(value)
    return Path(raw).expanduser()


def _num(value: Any, default: float, *, name: str, min_v: float | None = None) -> float:
    if value is None:
        value = default
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必须是数字，收到 {value!r}") from exc
    if min_v is not None and n < min_v:
        raise ConfigError(f"{name} 不能小于 {min_v}")
    return n


def _int(value: Any, default: int, *, name: str, min_v: int | None = None) -> int:
    return int(_num(value, default, name=name, min_v=min_v))


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _bool_opt(value: Any) -> bool | None:
    if value is None:
        return None
    return _bool(value, False)


def _str_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    raise ConfigError("列表配置必须是 YAML 列表或逗号分隔字符串")


@dataclass
class PathsConfig:
    recordings: Path = Path("recordings")
    output: Path = Path("output/clips")
    data: Path = Path("data")
    cookies: Path = Path("cookies.txt")
    browser_profile: Path = Path("data/browser-profile")

    def ensure(self) -> None:
        self.recordings.mkdir(parents=True, exist_ok=True)
        self.output.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.browser_profile.mkdir(parents=True, exist_ok=True)
        (self.data / "jobs").mkdir(parents=True, exist_ok=True)


@dataclass
class HttpConfig:
    timeout_seconds: float = 25.0
    retries: int = 3
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )


@dataclass
class WatchConfig:
    poll_interval_seconds: float = 15.0
    timeout_seconds: float = 0.0


@dataclass
class RecordConfig:
    segment_seconds: int = 60
    resume_gap_seconds: float = 8.0
    max_seconds: float = 0.0
    prefer: str = "auto"


@dataclass
class TranscribeConfig:
    model: str = "small"
    language: str = "zh"
    word_timestamps: bool = True
    vad_filter: bool = True
    compute_type: str = "int8"
    device: str = "cpu"


@dataclass
class DetectWeights:
    energy: float = 1.0
    flux: float = 0.8
    speech: float = 0.6
    scene: float = 0.5
    keywords: float = 1.4
    chat: float = 1.0


@dataclass
class DetectConfig:
    min_clip_seconds: float = 8.0
    max_clip_seconds: float = 45.0
    merge_gap_seconds: float = 4.0
    pad_before_seconds: float = 1.5
    pad_after_seconds: float = 2.0
    audio_window_seconds: float = 0.25
    audio_percentile: float = 90.0
    scene_threshold: float = 0.35
    max_clips: int = 5
    snap_max_seconds: float = 0.35
    chat_events_file: Path | None = None
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    weights: DetectWeights = field(default_factory=DetectWeights)


@dataclass
class EditConfig:
    aspect: str = "9:16"
    fill: str = "blur"
    width: int = 1080
    height: int = 1920
    loudness_i: float = -14.0
    title_card: bool = True
    title_card_seconds: float = 1.2
    source_caption: bool = True
    caption_style: str = "douyin"
    style: str = "douyin_hot"
    zoom_punch: bool | None = None
    shake: bool | None = None
    jumpcut: bool | None = None
    keyword_pop: bool | None = None
    hook_seconds: float = 1.2
    punch_seconds: float = 0.45
    silence_speed: float = 1.12
    xfade_seconds: float = 0.25
    caption_mask: bool = True
    fade_in: bool = True
    bgm: Path | None = None
    whisper: bool = True
    whisper_model: str = "small"


@dataclass
class PublishConfig:
    url: str = "https://creator.douyin.com/creator-micro/content/upload"
    mode: str = "draft"
    visibility: str = "public"
    headed: bool = True
    timeout_seconds: float = 180.0


@dataclass
class AppConfig:
    paths: PathsConfig = field(default_factory=PathsConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    edit: EditConfig = field(default_factory=EditConfig)
    publish: PublishConfig = field(default_factory=PublishConfig)
    source: Path | None = None

    def validate(self) -> None:
        if self.detect.min_clip_seconds <= 0:
            raise ConfigError("detect.min_clip_seconds 必须 > 0")
        if self.detect.max_clip_seconds < self.detect.min_clip_seconds:
            raise ConfigError("detect.max_clip_seconds 必须 >= min_clip_seconds")
        if self.record.prefer not in {"auto", "ytdlp", "ffmpeg"}:
            raise ConfigError("record.prefer 只能是 auto / ytdlp / ffmpeg")
        if self.edit.fill not in {"blur", "crop"}:
            raise ConfigError("edit.fill 只能是 blur / crop")
        if self.edit.style not in {"douyin_hot", "clean", "party"}:
            raise ConfigError("edit.style 只能是 douyin_hot / clean / party")
        if self.edit.caption_style not in {"hormozi", "douyin", "standard"}:
            raise ConfigError("edit.caption_style 只能是 hormozi / douyin / standard")
        if self.publish.mode not in {"draft", "publish"}:
            raise ConfigError("publish.mode 只能是 draft / publish")
        if self.publish.visibility not in {"public", "friends", "private"}:
            raise ConfigError("publish.visibility 只能是 public / friends / private")
        if self.record.segment_seconds < 5:
            raise ConfigError("record.segment_seconds 太短（至少 5 秒）")
        if not (1.05 <= self.edit.silence_speed <= 1.25):
            raise ConfigError("edit.silence_speed 应在 1.05–1.25（party 静音加速）")


def find_config_path(explicit: Path | None = None) -> Path | None:
    if explicit:
        return explicit
    env = os.environ.get("DYLIVE_CONFIG")
    if env:
        return Path(env).expanduser()
    cwd = Path.cwd() / DEFAULT_CONFIG_NAME
    if cwd.is_file():
        return cwd
    return None


def load_config(path: Path | None = None) -> AppConfig:
    cfg_path = find_config_path(path)
    raw: dict[str, Any] = {}
    if cfg_path is not None:
        if not cfg_path.is_file():
            raise ConfigError(f"找不到配置文件: {cfg_path}")
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigError("配置文件必须是 YAML 映射")
        raw = loaded

    paths_raw = raw.get("paths") or {}
    cookies = os.environ.get("DYLIVE_COOKIES") or paths_raw.get("cookies") or "cookies.txt"
    cfg = AppConfig(
        paths=PathsConfig(
            recordings=_as_path(paths_raw.get("recordings"), "recordings"),
            output=_as_path(paths_raw.get("output"), "output/clips"),
            data=_as_path(paths_raw.get("data"), "data"),
            cookies=_as_path(cookies, "cookies.txt"),
            browser_profile=_as_path(paths_raw.get("browser_profile"), "data/browser-profile"),
        ),
        http=_http(raw.get("http") or {}),
        watch=_watch(raw.get("watch") or {}),
        record=_record(raw.get("record") or {}),
        transcribe=_transcribe(raw.get("transcribe") or {}),
        detect=_detect(raw.get("detect") or {}),
        edit=_edit(raw.get("edit") or {}),
        publish=_publish(raw.get("publish") or {}),
        source=cfg_path,
    )
    cfg.validate()
    return cfg


def _http(raw: dict[str, Any]) -> HttpConfig:
    return HttpConfig(
        timeout_seconds=_num(raw.get("timeout_seconds"), 25, name="http.timeout_seconds", min_v=1),
        retries=_int(raw.get("retries"), 3, name="http.retries", min_v=0),
        user_agent=str(raw.get("user_agent") or HttpConfig.user_agent),
    )


def _watch(raw: dict[str, Any]) -> WatchConfig:
    return WatchConfig(
        poll_interval_seconds=_num(
            raw.get("poll_interval_seconds"), 15, name="watch.poll_interval_seconds", min_v=1
        ),
        timeout_seconds=_num(
            raw.get("timeout_seconds"), 0, name="watch.timeout_seconds", min_v=0
        ),
    )


def _record(raw: dict[str, Any]) -> RecordConfig:
    return RecordConfig(
        segment_seconds=_int(raw.get("segment_seconds"), 60, name="record.segment_seconds", min_v=5),
        resume_gap_seconds=_num(
            raw.get("resume_gap_seconds"), 8, name="record.resume_gap_seconds", min_v=0
        ),
        max_seconds=_num(raw.get("max_seconds"), 0, name="record.max_seconds", min_v=0),
        prefer=str(raw.get("prefer") or "auto"),
    )


def _transcribe(raw: dict[str, Any]) -> TranscribeConfig:
    return TranscribeConfig(
        model=str(raw.get("model") or "small"),
        language=str(raw.get("language") or "zh"),
        word_timestamps=_bool(raw.get("word_timestamps"), True),
        vad_filter=_bool(raw.get("vad_filter"), True),
        compute_type=str(raw.get("compute_type") or "int8"),
        device=str(raw.get("device") or "cpu"),
    )


def _detect(raw: dict[str, Any]) -> DetectConfig:
    chat = raw.get("chat_events_file")
    w = raw.get("weights") or {}
    return DetectConfig(
        min_clip_seconds=_num(raw.get("min_clip_seconds"), 8, name="detect.min_clip_seconds", min_v=0.1),
        max_clip_seconds=_num(raw.get("max_clip_seconds"), 45, name="detect.max_clip_seconds", min_v=0.1),
        merge_gap_seconds=_num(raw.get("merge_gap_seconds"), 4, name="detect.merge_gap_seconds", min_v=0),
        pad_before_seconds=_num(raw.get("pad_before_seconds"), 1.5, name="detect.pad_before_seconds", min_v=0),
        pad_after_seconds=_num(raw.get("pad_after_seconds"), 2.0, name="detect.pad_after_seconds", min_v=0),
        audio_window_seconds=_num(
            raw.get("audio_window_seconds"), 0.25, name="detect.audio_window_seconds", min_v=0.05
        ),
        audio_percentile=_num(raw.get("audio_percentile"), 90, name="detect.audio_percentile", min_v=1),
        scene_threshold=_num(raw.get("scene_threshold"), 0.35, name="detect.scene_threshold", min_v=0.01),
        max_clips=_int(raw.get("max_clips"), 5, name="detect.max_clips", min_v=1),
        snap_max_seconds=_num(raw.get("snap_max_seconds"), 0.35, name="detect.snap_max_seconds", min_v=0.05),
        chat_events_file=Path(chat).expanduser() if chat else None,
        keywords=_str_list(raw.get("keywords"), DEFAULT_KEYWORDS),
        weights=DetectWeights(
            energy=_num(w.get("energy"), 1.0, name="detect.weights.energy", min_v=0),
            flux=_num(w.get("flux"), 0.8, name="detect.weights.flux", min_v=0),
            speech=_num(w.get("speech"), 0.6, name="detect.weights.speech", min_v=0),
            scene=_num(w.get("scene"), 0.5, name="detect.weights.scene", min_v=0),
            keywords=_num(w.get("keywords"), 1.4, name="detect.weights.keywords", min_v=0),
            chat=_num(w.get("chat"), 1.0, name="detect.weights.chat", min_v=0),
        ),
    )


def _edit(raw: dict[str, Any]) -> EditConfig:
    return EditConfig(
        aspect=str(raw.get("aspect") or "9:16"),
        fill=str(raw.get("fill") or "blur"),
        width=_int(raw.get("width"), 1080, name="edit.width", min_v=160),
        height=_int(raw.get("height"), 1920, name="edit.height", min_v=160),
        loudness_i=_num(raw.get("loudness_i"), -14, name="edit.loudness_i"),
        title_card=_bool(raw.get("title_card"), True),
        title_card_seconds=_num(
            raw.get("title_card_seconds"), 1.2, name="edit.title_card_seconds", min_v=0.3
        ),
        source_caption=_bool(raw.get("source_caption"), True),
        caption_style=str(raw.get("caption_style") or "douyin"),
        style=str(raw.get("style") or "douyin_hot"),
        zoom_punch=_bool_opt(raw.get("zoom_punch")),
        shake=_bool_opt(raw.get("shake")),
        jumpcut=_bool_opt(raw.get("jumpcut")),
        keyword_pop=_bool_opt(raw.get("keyword_pop")),
        hook_seconds=_num(raw.get("hook_seconds"), 1.2, name="edit.hook_seconds", min_v=0.3),
        punch_seconds=_num(raw.get("punch_seconds"), 0.45, name="edit.punch_seconds", min_v=0.25),
        silence_speed=_num(raw.get("silence_speed"), 1.12, name="edit.silence_speed", min_v=1.05),
        xfade_seconds=_num(raw.get("xfade_seconds"), 0.25, name="edit.xfade_seconds", min_v=0.05),
        caption_mask=_bool(raw.get("caption_mask"), True),
        fade_in=_bool(raw.get("fade_in"), True),
        bgm=Path(raw["bgm"]).expanduser() if raw.get("bgm") else None,
        whisper=_bool(raw.get("whisper"), True),
        whisper_model=str(raw.get("whisper_model") or raw.get("model") or "small"),
    )


def _publish(raw: dict[str, Any]) -> PublishConfig:
    return PublishConfig(
        url=str(raw.get("url") or PublishConfig.url),
        mode=str(raw.get("mode") or "draft"),
        visibility=str(raw.get("visibility") or "public"),
        headed=_bool(raw.get("headed"), True),
        timeout_seconds=_num(
            raw.get("timeout_seconds"), 180, name="publish.timeout_seconds", min_v=30
        ),
    )
