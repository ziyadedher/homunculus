from pathlib import Path

import aiosqlite
import pytest

from homunculus.storage.store import open_store
from homunculus.utils.config import (
    AnthropicConfig,
    Config,
    OwnerConfig,
    StorageConfig,
)


@pytest.fixture
def config() -> Config:
    return Config(
        owner=OwnerConfig(
            name="TestOwner",
            email="test@example.com",
            timezone="America/Los_Angeles",
            telegram_chat_id="999000",
        ),
        anthropic=AnthropicConfig(model="claude-sonnet-4-20250514", api_key="test_key"),
        storage=StorageConfig(db_path=Path("data/homunculus.db")),
    )


@pytest.fixture
async def db(tmp_path: Path) -> aiosqlite.Connection:
    db_path = tmp_path / "test.db"
    conn = await open_store(db_path)
    yield conn
    await conn.close()


@pytest.fixture
def contact() -> dict[str, object]:
    return {
        "contact_id": "test_contact_123",
        "name": "Alice",
        "phone": "+11234567890",
        "email": "alice@test.com",
        "timezone": "America/New_York",
        "notes": "Test contact",
        "telegram_chat_id": "123456789",
        "created_at": "2025-01-01 00:00:00",
    }
