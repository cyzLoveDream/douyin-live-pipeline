from pathlib import Path

import pytest
import yaml

from dylive.config import load_config
from dylive.exceptions import ConfigError


def test_default_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(None)
    assert cfg.detect.min_clip_seconds == 8
    assert cfg.detect.max_clip_seconds == 45
    assert cfg.publish.mode == "draft"
    assert cfg.publish.url.endswith("/content/upload")


def test_rejects_min_gt_max(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"detect": {"min_clip_seconds": 40, "max_clip_seconds": 10}}))
    with pytest.raises(ConfigError):
        load_config(p)


def test_cookies_env_override(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("paths: {cookies: ignored.txt}\n")
    monkeypatch.setenv("DYLIVE_COOKIES", str(tmp_path / "from-env.txt"))
    cfg = load_config(p)
    assert cfg.paths.cookies == Path(tmp_path / "from-env.txt")


def test_edit_style_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(None)
    assert cfg.edit.style == "douyin_hot"
    assert cfg.edit.caption_style == "douyin"
    assert cfg.transcribe.model == "small"
    assert cfg.transcribe.language == "zh"
    assert "买它" in cfg.detect.keywords
    assert cfg.detect.max_clips == 5
    assert cfg.detect.weights.keywords == 1.4
