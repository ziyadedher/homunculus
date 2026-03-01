import os
from pathlib import Path
from unittest.mock import patch

import pytest

from homunculus.utils.config import (
    LoggingConfig,
    TracingConfig,
    load_config,
)

MINIMAL_TOML = b"""\
[owner]
name = "Test"
email = "test@example.com"
timezone = "UTC"
telegram_chat_id = "999000"

[anthropic]
model = "claude-sonnet-4-20250514"
"""

FULL_TOML = b"""\
[owner]
name = "Test"
email = "test@example.com"
timezone = "UTC"
telegram_chat_id = "999000"

[telegram]

[anthropic]
model = "claude-sonnet-4-20250514"

[storage]
db_path = "data/homunculus.db"

[google_calendar]
calendar_id = "primary"
credentials_path = "data/credentials.json"
token_path = "data/token.json"

[server]
host = "127.0.0.1"
port = 9090

[logging]
level = "DEBUG"
format = "json"

[tracing]
enabled = true
endpoint = "http://otel:4318/v1/traces"
console_export = false
service_name = "test-svc"
"""

MINIMAL_ENV = {
    "ANTHROPIC_API_KEY": "key",
}

FULL_ENV = {
    "TELEGRAM_BOT_TOKEN": "bot_token_123",
    "ANTHROPIC_API_KEY": "key",
}


def test_load_minimal_config(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        cfg = load_config(cfg_path)

    assert cfg.owner.name == "Test"
    assert cfg.anthropic.api_key == "key"
    assert cfg.telegram is None
    assert cfg.google_calendar is None
    assert cfg.google_maps is None
    assert cfg.storage.db_path == Path("data/homunculus.db")
    # Defaults
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8080
    assert cfg.logging == LoggingConfig()
    assert cfg.tracing == TracingConfig()


def test_load_full_config(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(FULL_TOML)

    with patch.dict(os.environ, FULL_ENV):
        cfg = load_config(cfg_path)

    assert cfg.telegram is not None
    assert cfg.telegram.bot_token == "bot_token_123"
    assert cfg.google_calendar is not None
    assert cfg.google_calendar.calendar_id == "primary"
    assert cfg.google_calendar.credentials_path == Path("data/credentials.json")
    assert cfg.google_calendar.token_path == Path("data/token.json")
    assert cfg.storage.db_path == Path("data/homunculus.db")
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 9090
    assert cfg.logging.level == "DEBUG"
    assert cfg.logging.format == "json"
    assert cfg.tracing.enabled is True
    assert cfg.tracing.service_name == "test-svc"
    assert cfg.tracing.console_export is False


def test_config_is_frozen(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        cfg = load_config(cfg_path)

    with pytest.raises(AttributeError):
        setattr(cfg, "owner", None)  # noqa: B010


def test_missing_env_var_raises(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, {}, clear=True):
        try:
            load_config(cfg_path)
            raise AssertionError("Should have raised")
        except KeyError:
            pass  # expected


def test_telegram_without_env_vars_raises(tmp_path):
    """If [telegram] section is present but env vars missing, should raise."""
    toml = MINIMAL_TOML + b"\n[telegram]\n"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(toml)

    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        try:
            load_config(cfg_path)
            raise AssertionError("Should have raised")
        except KeyError:
            pass  # expected — TELEGRAM_BOT_TOKEN missing


def test_conversation_config_defaults(tmp_path):
    """ConversationConfig defaults work when [conversation] section is absent."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        cfg = load_config(cfg_path)

    assert cfg.conversation.ttl_minutes == 5
    assert cfg.conversation.approval_ttl_minutes == 1440


def test_google_maps_config_from_env(tmp_path):
    """GoogleMapsConfig loaded when GOOGLE_MAPS_API_KEY env var is set."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    env = {**MINIMAL_ENV, "GOOGLE_MAPS_API_KEY": "maps_key"}
    with patch.dict(os.environ, env, clear=True):
        cfg = load_config(cfg_path)

    assert cfg.google_maps is not None
    assert cfg.google_maps.api_key == "maps_key"


def test_google_maps_config_from_toml(tmp_path):
    """GoogleMapsConfig loaded when [google_maps] section present."""
    toml = MINIMAL_TOML + b"\n[google_maps]\n"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(toml)

    env = {**MINIMAL_ENV, "GOOGLE_MAPS_API_KEY": "maps_key"}
    with patch.dict(os.environ, env, clear=True):
        cfg = load_config(cfg_path)

    assert cfg.google_maps is not None
    assert cfg.google_maps.api_key == "maps_key"


def test_google_maps_config_none_without_env(tmp_path):
    """GoogleMapsConfig is None when env var is not set."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        cfg = load_config(cfg_path)

    assert cfg.google_maps is None
