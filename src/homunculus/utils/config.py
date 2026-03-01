import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

LogFormat = Literal["console", "json"]


@dataclass(frozen=True)
class OwnerConfig:
    name: str
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
    credentials_path: Path
    token_path: Path


@dataclass(frozen=True)
class StorageConfig:
    db_path: Path


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


@dataclass(frozen=True)
class GoogleMapsConfig:
    api_key: str


@dataclass(frozen=True)
class Config:
    owner: OwnerConfig
    anthropic: AnthropicConfig
    storage: StorageConfig
    telegram: TelegramConfig | None = None
    google_calendar: GoogleCalendarConfig | None = None
    google_maps: GoogleMapsConfig | None = None
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)


def load_config(path: str | Path = "config/config.toml") -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    telegram_section = raw.get("telegram")
    telegram = None
    if telegram_section is not None or os.environ.get("TELEGRAM_BOT_TOKEN"):
        telegram = TelegramConfig(
            bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        )

    gcal_section = raw.get("google_calendar")
    google_calendar = None
    if gcal_section is not None:
        google_calendar = GoogleCalendarConfig(
            calendar_id=gcal_section["calendar_id"],
            credentials_path=Path(gcal_section["credentials_path"]),
            token_path=Path(gcal_section["token_path"]),
        )

    google_maps = None
    gmaps_section = raw.get("google_maps")
    if gmaps_section is not None or os.environ.get("GOOGLE_MAPS_API_KEY"):
        google_maps = GoogleMapsConfig(api_key=os.environ["GOOGLE_MAPS_API_KEY"])

    storage_section = raw.get("storage", {})
    storage = StorageConfig(
        db_path=Path(storage_section.get("db_path", "data/homunculus.db")),
    )

    return Config(
        owner=OwnerConfig(
            name=raw["owner"]["name"],
            timezone=raw["owner"]["timezone"],
            telegram_chat_id=raw["owner"]["telegram_chat_id"],
        ),
        anthropic=AnthropicConfig(
            model=raw["anthropic"]["model"],
            api_key=os.environ["ANTHROPIC_API_KEY"],
        ),
        storage=storage,
        telegram=telegram,
        google_calendar=google_calendar,
        google_maps=google_maps,
        server=ServerConfig(
            host=raw.get("server", {}).get("host", "0.0.0.0"),
            port=raw.get("server", {}).get("port", 8080),
            webhook_base_url=raw.get("server", {}).get("webhook_base_url"),
        ),
        logging=LoggingConfig(
            level=raw.get("logging", {}).get("level", "INFO"),
            format=raw.get("logging", {}).get("format", "console"),
        ),
        tracing=TracingConfig(
            enabled=raw.get("tracing", {}).get("enabled", False),
            endpoint=raw.get("tracing", {}).get("endpoint", "http://localhost:4318/v1/traces"),
            console_export=raw.get("tracing", {}).get("console_export", True),
            service_name=raw.get("tracing", {}).get("service_name", "homunculus"),
        ),
        conversation=ConversationConfig(
            ttl_minutes=raw.get("conversation", {}).get("ttl_minutes", 5),
            approval_ttl_minutes=raw.get("conversation", {}).get("approval_ttl_minutes", 1440),
        ),
    )
