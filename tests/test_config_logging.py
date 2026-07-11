"""Config parsing, env overrides, logging disable + rotation, URL redaction."""

from __future__ import annotations

import logging

from grabbit.config import Config, load_config
from grabbit.logging_setup import redact_url, setup_logging


def test_defaults():
    cfg = Config()
    assert cfg.engine.channel == "stable"
    assert cfg.security.require_auth is True
    assert cfg.security.update_check is False
    assert cfg.logging.enabled is True


def test_yaml_parsing(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "server:\n  port: 9999\n"
        "engine:\n  channel: dev\n"
        "logging:\n  enabled: false\n"
        "downloads:\n  max_concurrent: 8\n"
    )
    cfg = load_config(p)
    assert cfg.server.port == 9999
    assert cfg.engine.channel == "dev"
    assert cfg.logging.enabled is False
    assert cfg.downloads.max_concurrent == 8


def test_env_overrides(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("server:\n  port: 1000\n")
    monkeypatch.setenv("GRABBIT_PORT", "2000")
    monkeypatch.setenv("GRABBIT_ENGINE_CHANNEL", "dev")
    cfg = load_config(p)
    assert cfg.server.port == 2000
    assert cfg.engine.channel == "dev"


def test_invalid_channel_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("engine:\n  channel: nightly\n")
    try:
        load_config(p)
        raise AssertionError("expected validation error")
    except ValueError:
        pass


def test_logging_disabled_writes_no_files(tmp_path):
    cfg = Config.model_validate({
        "data_dir": str(tmp_path),
        "logging": {"enabled": False},
    })
    setup_logging(cfg.logging, cfg.data_dir)
    logging.getLogger("grabbit.test").error("this must go nowhere")
    assert not list(tmp_path.glob("*.log*"))


def test_logging_rotation(tmp_path):
    cfg = Config.model_validate({
        "data_dir": str(tmp_path),
        "logging": {
            "enabled": True, "format": "text",
            "rotation": {"max_size_mb": 1, "max_files": 2, "compress": True},
        },
    })
    setup_logging(cfg.logging, cfg.data_dir)
    log = logging.getLogger("grabbit.rot")
    payload = "x" * 10000
    for _ in range(150):  # ~1.5 MB
        log.info(payload)
    logging.getLogger().handlers.clear()  # release file handles
    files = sorted(p.name for p in tmp_path.glob("grabbit.log*"))
    assert "grabbit.log" in files
    assert any(f.endswith(".gz") for f in files), files


def test_redact_url():
    url = "https://cdn.example.com/f.mp4?token=SECRET&expires=123&size=big"
    red = redact_url(url)
    assert "SECRET" not in red and "123" not in red
    assert "size=big" in red
    assert redact_url("https://example.com/plain") == "https://example.com/plain"
