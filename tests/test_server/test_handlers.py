from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from homunculus.agent.loop import AgentResult
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.router import MessageRouter
from homunculus.server.app import handle_health
from homunculus.server.handlers import handle_api_get_approval, handle_api_message
from homunculus.storage import store
from homunculus.storage.store import open_store
from homunculus.types import ApprovalId, ApprovalStatus, ChannelId, ConversationId
from homunculus.utils.config import (
    AnthropicConfig,
    GoogleConfig,
    OwnerConfig,
    ServeConfig,
    StorageConfig,
    TelegramConfig,
)

from .conftest import MockHttpSession

VALID_TOKEN = "valid_google_access_token"


def _make_config() -> ServeConfig:
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
async def api_app(tmp_path: Path) -> web.Application:
    """Create a minimal aiohttp app with API routes for testing."""
    config = _make_config()
    db = await open_store(tmp_path / "test.db")

    app = web.Application()
    app["config"] = config
    app["db"] = db

    # Mock HTTP session with tokeninfo responses
    app["http_session"] = MockHttpSession(
        {
            VALID_TOKEN: (200, "test@example.com"),
        }
    )

    registry = ToolRegistry()
    app["registry"] = registry

    channel = AsyncMock()
    channel.channel_id = "telegram"
    app["channel"] = channel

    channels = {ChannelId("telegram"): channel}
    router = MessageRouter(config=config, db=db, registry=registry, channels=channels)
    app["router"] = router

    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/message", handle_api_message)
    app.router.add_get("/api/approvals/{id}", handle_api_get_approval)

    yield app

    await db.close()


@pytest.fixture
async def client(api_app: web.Application, aiohttp_client) -> TestClient:
    return await aiohttp_client(api_app)


async def test_health(client: TestClient):
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"


async def test_api_message_no_auth(client: TestClient):
    resp = await client.post("/api/message", json={"conversation_id": "cli:alice", "body": "hi"})
    assert resp.status == 401


async def test_api_message_bad_token(client: TestClient):
    resp = await client.post(
        "/api/message",
        json={"conversation_id": "cli:alice", "body": "hi"},
        headers={"Authorization": "Bearer bad_token"},
    )
    assert resp.status == 401


async def test_api_message_non_owner(client: TestClient, api_app: web.Application):
    """Non-owner can send messages (AuthN only, no AuthZ on /api/message)."""
    api_app["http_session"] = MockHttpSession(
        {
            VALID_TOKEN: (200, "test@example.com"),
            "other_token": (200, "other@example.com"),
        }
    )

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hello!")
        resp = await client.post(
            "/api/message",
            json={"conversation_id": "cli:alice", "body": "hi"},
            headers={"Authorization": "Bearer other_token"},
        )

    assert resp.status == 200
    data = await resp.json()
    assert data["response_text"] == "Hello!"


async def test_api_message_success(client: TestClient):
    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hello from agent!")
        resp = await client.post(
            "/api/message",
            json={"conversation_id": "cli:alice", "body": "hello"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status == 200
    data = await resp.json()
    assert data["response_text"] == "Hello from agent!"
    mock_agent.assert_called_once()


async def test_api_message_missing_body(client: TestClient):
    resp = await client.post(
        "/api/message",
        json={"conversation_id": "cli:alice"},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status == 400


async def test_api_message_with_escalation(client: TestClient):
    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(
            response_text="Checking with owner...",
            escalation_message="Approval needed: create_event",
            escalation_approval_id=ApprovalId("abc123"),
        )
        resp = await client.post(
            "/api/message",
            json={"conversation_id": "cli:alice", "body": "create event"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status == 200
    data = await resp.json()
    assert data["response_text"] == "Checking with owner..."
    assert data["escalation_message"] == "Approval needed: create_event"
    assert data["approval_id"] == "abc123"


async def test_api_get_approval_not_found(client: TestClient):
    resp = await client.get(
        "/api/approvals/nonexistent",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status == 404


async def test_api_get_approval_pending(client: TestClient, api_app: web.Application):
    db = api_app["db"]
    approval_id = await store.create_approval(
        db,
        ConversationId("cli:alice"),
        "Create lunch",
        "create_event",
        {"summary": "Lunch"},
    )

    resp = await client.get(
        f"/api/approvals/{approval_id}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )

    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "pending"
    assert data["response_text"] is None


async def test_api_get_approval_resolved_with_response(
    client: TestClient, api_app: web.Application
):
    db = api_app["db"]
    approval_id = await store.create_approval(
        db,
        ConversationId("cli:alice"),
        "Create lunch",
        "create_event",
        {"summary": "Lunch"},
    )
    await store.resolve_approval(db, approval_id, ApprovalStatus.APPROVED)
    await store.save_approval_response(db, approval_id, "Lunch event created!")
    await store.complete_approval(db, approval_id)

    resp = await client.get(
        f"/api/approvals/{approval_id}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )

    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "completed"
    assert data["response_text"] == "Lunch event created!"


async def test_api_get_approval_no_auth(client: TestClient):
    resp = await client.get("/api/approvals/some_id")
    assert resp.status == 401


async def test_api_get_approval_non_owner(client: TestClient, api_app: web.Application):
    """Non-owner is authenticated but forbidden from polling approvals."""
    api_app["http_session"] = MockHttpSession({"other_token": (200, "other@example.com")})

    resp = await client.get(
        "/api/approvals/some_id",
        headers={"Authorization": "Bearer other_token"},
    )
    assert resp.status == 403
