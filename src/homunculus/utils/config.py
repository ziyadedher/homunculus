import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_type_hints

LogFormat = Literal["console", "json"]


@dataclass(frozen=True)
class OwnerConfig:
    name: str
    email: str
    timezone: str
    telegram_chat_id: str


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str


@dataclass(frozen=True)
class AnthropicConfig:
    model: str
    api_key: str


@dataclass(frozen=True)
class GoogleCalendarConfig:
    calendar_id: str


@dataclass(frozen=True)
class GoogleMapsConfig:
    api_key: str


@dataclass(frozen=True)
class GoogleEmailConfig:
    pass


@dataclass(frozen=True)
class GoogleConfig:
    credentials_path: Path = Path("data/google_credentials.json")
    token_path: Path = Path("data/google_token.json")
    calendar: GoogleCalendarConfig | None = None
    email: GoogleEmailConfig | None = None
    maps: GoogleMapsConfig | None = None


@dataclass(frozen=True)
class StorageConfig:
    db_path: Path = Path("data/data.db")


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    webhook_base_url: str | None = None


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    format: LogFormat = "console"


@dataclass(frozen=True)
class TracingConfig:
    enabled: bool = False
    endpoint: str = "http://localhost:4318/v1/traces"
    console_export: bool = True
    service_name: str = "homunculus"


@dataclass(frozen=True)
class ConversationConfig:
    ttl_minutes: int = 1440
    approval_ttl_minutes: int = 1440


@dataclass(frozen=True, kw_only=True)
class _BaseConfig:
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)


@dataclass(frozen=True)
class ClientConfig(_BaseConfig):
    server_url: str = "http://localhost:8080"
    credentials_path: Path = Path("~/.config/homunculus/credentials.json")


@dataclass(frozen=True)
class ServeConfig(_BaseConfig):
    owner: OwnerConfig
    anthropic: AnthropicConfig
    google: GoogleConfig
    storage: StorageConfig
    telegram: TelegramConfig
    server: ServerConfig = field(default_factory=ServerConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)


def _from_toml[T](cls: type[T], section: dict[str, object], **overrides: object) -> T:
    """Build a frozen dataclass from a TOML section dict.

    Converts str values to Path for fields annotated as Path.
    Skips nested dict values (TOML sub-sections).
    Missing keys use dataclass defaults. Overrides are applied last.
    """
    hints = get_type_hints(cls)
    kwargs: dict[str, object] = {}
    for key, value in section.items():
        if isinstance(value, dict):
            continue
        if hints.get(key) is Path and isinstance(value, str):
            value = Path(value)
        kwargs[key] = value
    kwargs.update(overrides)
    return cls(**kwargs)


def _load_toml(path: str | Path) -> dict[str, object]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _section(raw: dict[str, object], key: str) -> dict[str, object]:
    """Extract a TOML section as a dict, defaulting to empty."""
    value = raw.get(key, {})
    return value if isinstance(value, dict) else {}


def _parse_google_serve(raw: dict[str, object]) -> GoogleConfig:
    """Parse GoogleConfig from TOML + GOOGLE_MAPS_API_KEY env var."""
    google = _section(raw, "google")
    gcal_raw = google.get("calendar")
    email_raw = google.get("email")
    maps_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    return _from_toml(
        GoogleConfig,
        google,
        calendar=_from_toml(GoogleCalendarConfig, gcal_raw) if isinstance(gcal_raw, dict) else None,
        email=_from_toml(GoogleEmailConfig, email_raw) if isinstance(email_raw, dict) else None,
        maps=GoogleMapsConfig(api_key=maps_key) if maps_key is not None else None,
    )


def load_client_config(path: str | Path = "config/config.client.toml") -> ClientConfig:
    """Load config for CLI chat/auth. Reads [client] + [logging]/[tracing]."""
    raw = _load_toml(path)
    return _from_toml(
        ClientConfig,
        _section(raw, "client"),
        logging=_from_toml(LoggingConfig, _section(raw, "logging")),
        tracing=_from_toml(TracingConfig, _section(raw, "tracing")),
    )


def load_serve_config(path: str | Path = "config/config.server.toml") -> ServeConfig:
    """Load config for server mode.

    Requires:
        - ANTHROPIC_API_KEY
        - TELEGRAM_BOT_TOKEN

    Optional:
        - GOOGLE_MAPS_API_KEY
    """
    raw = _load_toml(path)

    return ServeConfig(
        owner=_from_toml(OwnerConfig, _section(raw, "owner")),
        anthropic=_from_toml(
            AnthropicConfig, _section(raw, "anthropic"), api_key=os.environ["ANTHROPIC_API_KEY"]
        ),
        google=_parse_google_serve(raw),
        storage=_from_toml(StorageConfig, _section(raw, "storage")),
        telegram=TelegramConfig(bot_token=os.environ["TELEGRAM_BOT_TOKEN"]),
        server=_from_toml(ServerConfig, _section(raw, "server")),
        logging=_from_toml(LoggingConfig, _section(raw, "logging")),
        tracing=_from_toml(TracingConfig, _section(raw, "tracing")),
        conversation=_from_toml(ConversationConfig, _section(raw, "conversation")),
    )
