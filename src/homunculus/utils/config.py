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
class GoogleGmailConfig:
    pass


@dataclass(frozen=True)
class GoogleConfig:
    credentials_path: Path = Path("data/google_credentials.json")
    token_path: Path = Path("data/google_token.json")
    calendar: GoogleCalendarConfig | None = None
    gmail: GoogleGmailConfig | None = None
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
    ttl_minutes: int = 5
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
class AdminConfig(_BaseConfig):
    storage: StorageConfig = field(default_factory=StorageConfig)
    owner_timezone: str = "UTC"


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


def _parse_google_serve(raw: dict[str, object]) -> GoogleConfig:
    """Parse GoogleConfig from TOML + GOOGLE_MAPS_API_KEY env var."""
    google_section = raw.get("google", {})
    gcal_raw = google_section.get("calendar") if isinstance(google_section, dict) else None
    gmail_raw = google_section.get("gmail") if isinstance(google_section, dict) else None
    maps_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    return _from_toml(
        GoogleConfig,
        google_section if isinstance(google_section, dict) else {},
        calendar=_from_toml(GoogleCalendarConfig, gcal_raw) if isinstance(gcal_raw, dict) else None,
        gmail=_from_toml(GoogleGmailConfig, gmail_raw) if isinstance(gmail_raw, dict) else None,
        maps=GoogleMapsConfig(api_key=maps_key) if maps_key is not None else None,
    )


def load_client_config(path: str | Path = "config/config.toml") -> ClientConfig:
    """Load config for CLI chat/auth. Reads [client] + [logging]/[tracing]."""
    raw = _load_toml(path)
    client_section = raw.get("client", {})
    return _from_toml(
        ClientConfig,
        client_section if isinstance(client_section, dict) else {},
        logging=_from_toml(LoggingConfig, raw.get("logging", {})),
        tracing=_from_toml(TracingConfig, raw.get("tracing", {})),
    )


def load_admin_config(path: str | Path = "config/config.toml") -> AdminConfig:
    """Load config for admin commands (direct DB access). No env vars needed."""
    raw = _load_toml(path)
    owner_section = raw.get("owner", {})
    owner_tz = owner_section.get("timezone", "UTC") if isinstance(owner_section, dict) else "UTC"
    return AdminConfig(
        storage=_from_toml(StorageConfig, raw.get("storage", {})),
        owner_timezone=owner_tz,
        logging=_from_toml(LoggingConfig, raw.get("logging", {})),
        tracing=_from_toml(TracingConfig, raw.get("tracing", {})),
    )


def load_serve_config(path: str | Path = "config/config.toml") -> ServeConfig:
    """Load config for server mode.

    Requires:
        - ANTHROPIC_API_KEY
        - TELEGRAM_BOT_TOKEN

    Optional:
        - GOOGLE_MAPS_API_KEY
    """
    raw = _load_toml(path)

    return ServeConfig(
        owner=_from_toml(OwnerConfig, raw["owner"]),
        anthropic=_from_toml(
            AnthropicConfig, raw["anthropic"], api_key=os.environ["ANTHROPIC_API_KEY"]
        ),
        google=_parse_google_serve(raw),
        storage=_from_toml(StorageConfig, raw.get("storage", {})),
        telegram=TelegramConfig(bot_token=os.environ["TELEGRAM_BOT_TOKEN"]),
        server=_from_toml(ServerConfig, raw.get("server", {})),
        logging=_from_toml(LoggingConfig, raw.get("logging", {})),
        tracing=_from_toml(TracingConfig, raw.get("tracing", {})),
        conversation=_from_toml(ConversationConfig, raw.get("conversation", {})),
    )
