import os
from pathlib import Path
from unittest.mock import patch

import pytest

from homunculus.utils.config import (
    LoggingConfig,
    TracingConfig,
    load_client_config,
    load_serve_config,
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

[google]
credentials_path = "data/credentials.json"
token_path = "data/token.json"

[google.calendar]
calendar_id = "primary"

[server]
host = "127.0.0.1"
port = 9090

[client]
server_url = "https://my-server.com"
credentials_path = "~/.config/homunculus/my-creds.json"

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
    "TELEGRAM_BOT_TOKEN": "bot_token_minimal",
}

FULL_ENV = {
    "TELEGRAM_BOT_TOKEN": "bot_token_123",
    "ANTHROPIC_API_KEY": "key",
}


# --- load_serve_config tests ---


def test_load_minimal_serve_config(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        cfg = load_serve_config(cfg_path)

    assert cfg.owner.name == "Test"
    assert cfg.anthropic.api_key == "key"
    assert cfg.telegram.bot_token == "bot_token_minimal"
    assert cfg.google.calendar is None
    assert cfg.google.maps is None
    assert cfg.storage.db_path == Path("data/data.db")
    # Defaults
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8080
    assert cfg.logging == LoggingConfig()
    assert cfg.tracing == TracingConfig()


def test_load_full_serve_config(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(FULL_TOML)

    with patch.dict(os.environ, FULL_ENV):
        cfg = load_serve_config(cfg_path)

    assert cfg.telegram is not None
    assert cfg.telegram.bot_token == "bot_token_123"
    assert cfg.google.credentials_path == Path("data/credentials.json")
    assert cfg.google.token_path == Path("data/token.json")
    assert cfg.google.calendar is not None  # explicitly configured in FULL_TOML
    assert cfg.google.calendar.calendar_id == "primary"
    assert cfg.storage.db_path == Path("data/homunculus.db")
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 9090
    assert cfg.logging.level == "DEBUG"
    assert cfg.logging.format == "json"
    assert cfg.tracing.enabled is True
    assert cfg.tracing.service_name == "test-svc"
    assert cfg.tracing.console_export is False


def test_serve_config_is_frozen(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        cfg = load_serve_config(cfg_path)

    with pytest.raises(AttributeError):
        setattr(cfg, "owner", None)  # noqa: B010


def test_missing_env_var_raises(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, {}, clear=True):
        try:
            load_serve_config(cfg_path)
            raise AssertionError("Should have raised")
        except KeyError:
            pass  # expected


def test_telegram_without_env_var_raises(tmp_path):
    """Missing TELEGRAM_BOT_TOKEN raises KeyError."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    env_no_telegram = {k: v for k, v in MINIMAL_ENV.items() if k != "TELEGRAM_BOT_TOKEN"}
    with patch.dict(os.environ, env_no_telegram, clear=True), pytest.raises(KeyError):
        load_serve_config(cfg_path)


def test_conversation_config_defaults(tmp_path):
    """ConversationConfig defaults work when [conversation] section is absent."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        cfg = load_serve_config(cfg_path)

    assert cfg.conversation.ttl_minutes == 1440
    assert cfg.conversation.approval_ttl_minutes == 1440


def test_google_maps_config_from_env(tmp_path):
    """GoogleMapsConfig loaded when GOOGLE_MAPS_API_KEY env var is set."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    env = {**MINIMAL_ENV, "GOOGLE_MAPS_API_KEY": "maps_key"}
    with patch.dict(os.environ, env, clear=True):
        cfg = load_serve_config(cfg_path)

    assert cfg.google.maps is not None
    assert cfg.google.maps.api_key == "maps_key"


def test_google_maps_config_from_toml(tmp_path):
    """GoogleMapsConfig loaded when [google.maps] section present."""
    toml = MINIMAL_TOML + b"\n[google.maps]\n"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(toml)

    env = {**MINIMAL_ENV, "GOOGLE_MAPS_API_KEY": "maps_key"}
    with patch.dict(os.environ, env, clear=True):
        cfg = load_serve_config(cfg_path)

    assert cfg.google.maps is not None
    assert cfg.google.maps.api_key == "maps_key"


def test_google_maps_config_none_without_env(tmp_path):
    """GoogleMapsConfig is None when env var is not set."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, MINIMAL_ENV, clear=True):
        cfg = load_serve_config(cfg_path)

    assert cfg.google.maps is None


# --- load_client_config tests ---


def test_load_client_config_defaults(tmp_path):
    """Client config with no [client] section uses defaults."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, {}, clear=True):
        cfg = load_client_config(cfg_path)

    assert cfg.server_url == "http://localhost:8080"
    assert cfg.credentials_path == Path("~/.config/homunculus/credentials.json")
    assert cfg.logging == LoggingConfig()
    assert cfg.tracing == TracingConfig()


def test_load_client_config_full(tmp_path):
    """Client config reads [client] section from TOML."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(FULL_TOML)

    with patch.dict(os.environ, {}, clear=True):
        cfg = load_client_config(cfg_path)

    assert cfg.server_url == "https://my-server.com"
    assert cfg.credentials_path == Path("~/.config/homunculus/my-creds.json")
    assert cfg.logging.level == "DEBUG"
    assert cfg.logging.format == "json"


def test_client_config_is_frozen(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes(MINIMAL_TOML)

    with patch.dict(os.environ, {}, clear=True):
        cfg = load_client_config(cfg_path)

    with pytest.raises(AttributeError):
        setattr(cfg, "server_url", "other")  # noqa: B010
