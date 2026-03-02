from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.router import MessageRouter
from homunculus.server.app import create_app
from homunculus.server.dependencies import AppState, get_state
from homunculus.storage.store import open_store
from homunculus.types import ChannelId
from homunculus.utils.config import (
    AnthropicConfig,
    GoogleCalendarConfig,
    GoogleConfig,
    GoogleEmailConfig,
    OwnerConfig,
    ServeConfig,
    ServerConfig,
    StorageConfig,
    TelegramConfig,
)

VALID_TOKEN = "valid_google_access_token"
OWNER_EMAIL = "test@example.com"


def make_config(
    credentials_path: Path | None = None,
    webhook_base_url: str | None = None,
) -> ServeConfig:
    google_kwargs = {}
    if credentials_path is not None:
        google_kwargs["credentials_path"] = credentials_path
        google_kwargs["calendar"] = GoogleCalendarConfig(calendar_id="primary")
        google_kwargs["email"] = GoogleEmailConfig()

    server_kwargs = {}
    if webhook_base_url is not None:
        server_kwargs["webhook_base_url"] = webhook_base_url

    return ServeConfig(
        owner=OwnerConfig(
            name="TestOwner",
            email=OWNER_EMAIL,
            timezone="America/Los_Angeles",
            telegram_chat_id="999000",
        ),
        anthropic=AnthropicConfig(model="claude-sonnet-4-20250514", api_key="test_key"),
        google=GoogleConfig(**google_kwargs),
        storage=StorageConfig(),
        telegram=TelegramConfig(bot_token="test_bot_token"),
        server=ServerConfig(**server_kwargs),
    )


class MockHttpxTransport(httpx.AsyncBaseTransport):
    """Mock httpx transport that returns tokeninfo responses based on access token."""

    def __init__(self, tokeninfo_responses: dict[str, tuple[int, str | None]]):
        self._responses = tokeninfo_responses

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("access_token", "")
        if token in self._responses:
            status, email = self._responses[token]
            if email:
                return httpx.Response(status, json={"email": email})
            return httpx.Response(status, json={"error": "invalid_token"})
        return httpx.Response(400, json={"error": "invalid_token"})


@pytest.fixture
async def api_app(tmp_path: Path):
    """Create a FastAPI app with API routes for testing."""
    config = make_config()
    db = await open_store(tmp_path / "test.db")

    registry = ToolRegistry()

    channel = AsyncMock()
    channel.channel_id = "telegram"
    channels = {ChannelId("telegram"): channel}
    router = MessageRouter(config=config, db=db, registry=registry, channels=channels)

    http_client = httpx.AsyncClient(transport=MockHttpxTransport({VALID_TOKEN: (200, OWNER_EMAIL)}))

    state = AppState(
        config=config,
        db=db,
        registry=registry,
        router=router,
        http_client=http_client,
        webhook_secret="test_secret",
    )

    app = create_app(config)

    # Override the app state dependency and also set it directly on app.state
    # (needed for handlers that access request.app.state.app_state directly)
    app.dependency_overrides[get_state] = lambda: state
    app.state.app_state = state

    yield app, state

    await db.close()
    await http_client.aclose()


@pytest.fixture
def client(api_app: tuple) -> TestClient:
    app, _state = api_app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
async def auth_app(tmp_path: Path):
    """Create a FastAPI app with auth routes for testing."""
    creds_path = tmp_path / "credentials.json"
    config = make_config(credentials_path=creds_path, webhook_base_url="https://example.com")
    db = await open_store(tmp_path / "test.db")

    registry = ToolRegistry()

    channel = AsyncMock()
    channel.channel_id = "telegram"
    channels = {ChannelId("telegram"): channel}
    router = MessageRouter(config=config, db=db, registry=registry, channels=channels)

    http_client = httpx.AsyncClient(
        transport=MockHttpxTransport({"owner_access_token": (200, OWNER_EMAIL)})
    )

    state = AppState(
        config=config,
        db=db,
        registry=registry,
        router=router,
        http_client=http_client,
        webhook_secret="test_secret",
    )

    app = create_app(config)
    app.dependency_overrides[get_state] = lambda: state
    app.state.app_state = state

    yield app, state

    await db.close()
    await http_client.aclose()


@pytest.fixture
def auth_client(auth_app: tuple) -> TestClient:
    app, _state = auth_app
    return TestClient(app, raise_server_exceptions=False)
