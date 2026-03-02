from pathlib import Path

import aiosqlite
import pytest

from homunculus.storage.store import open_store
from homunculus.types import Contact, ContactId
from homunculus.utils.config import (
    AnthropicConfig,
    GoogleConfig,
    OwnerConfig,
    ServeConfig,
    StorageConfig,
    TelegramConfig,
)


@pytest.fixture
def config() -> ServeConfig:
    return ServeConfig(
        owner=OwnerConfig(
            name="TestOwner",
            email="test@example.com",
            timezone="America/Los_Angeles",
            telegram_chat_id="999000",
        ),
        anthropic=AnthropicConfig(model="claude-sonnet-4-20250514", api_key="test_key"),
        google=GoogleConfig(),
        storage=StorageConfig(),
        telegram=TelegramConfig(bot_token="test_bot_token"),
    )


@pytest.fixture
async def db(tmp_path: Path) -> aiosqlite.Connection:
    db_path = tmp_path / "test.db"
    conn = await open_store(db_path)
    yield conn
    await conn.close()


@pytest.fixture
def contact() -> Contact:
    return Contact(
        contact_id=ContactId("test_contact_123"),
        name="Alice",
        phone="+11234567890",
        email="alice@test.com",
        timezone="America/New_York",
        notes="Test contact",
        telegram_chat_id="123456789",
    )
