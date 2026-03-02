from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from homunculus.agent.tools.registry import ToolRegistry
from homunculus.server.auth import (
    handle_auth_callback,
    handle_auth_start,
    handle_auth_status,
    handle_service_callback,
    handle_service_start,
    handle_service_status,
)
from homunculus.storage import store
from homunculus.storage.store import open_store
from homunculus.utils.config import (
    AnthropicConfig,
    GoogleCalendarConfig,
    GoogleConfig,
    GoogleGmailConfig,
    OwnerConfig,
    ServeConfig,
    ServerConfig,
    StorageConfig,
    TelegramConfig,
)

from .conftest import MockHttpSession


def _make_config(credentials_path: Path) -> ServeConfig:
    return ServeConfig(
        owner=OwnerConfig(
            name="TestOwner",
            email="test@example.com",
            timezone="UTC",
            telegram_chat_id="999000",
        ),
        anthropic=AnthropicConfig(model="claude-sonnet-4-20250514", api_key="test_key"),
        google=GoogleConfig(
            credentials_path=credentials_path,
            calendar=GoogleCalendarConfig(calendar_id="primary"),
            gmail=GoogleGmailConfig(),
        ),
        storage=StorageConfig(),
        telegram=TelegramConfig(bot_token="test_bot_token"),
        server=ServerConfig(webhook_base_url="https://example.com"),
    )


@pytest.fixture
async def auth_app(tmp_path: Path) -> web.Application:
    creds_path = tmp_path / "credentials.json"
    config = _make_config(creds_path)
    db = await open_store(tmp_path / "test.db")

    app = web.Application()
    app["config"] = config
    app["db"] = db
    app["http_session"] = MockHttpSession(
        {
            "owner_access_token": (200, "test@example.com"),
        }
    )

    app["registry"] = ToolRegistry()

    app.router.add_post("/auth/start", handle_auth_start)
    app.router.add_get("/auth/callback", handle_auth_callback)
    app.router.add_get("/auth/status/{session_id}", handle_auth_status)
    app.router.add_post("/auth/service/{service}/start", handle_service_start)
    app.router.add_get("/auth/service/{service}/callback", handle_service_callback)
    app.router.add_get("/auth/service/{service}/status/{session_id}", handle_service_status)

    yield app

    await db.close()


@pytest.fixture
async def auth_client(auth_app: web.Application, aiohttp_client) -> TestClient:
    return await aiohttp_client(auth_app)


async def test_auth_start(auth_client: TestClient):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/auth?state=xyz", "xyz")

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = await auth_client.post("/auth/start")

    assert resp.status == 200
    data = await resp.json()
    assert "session_id" in data
    assert "auth_url" in data
    assert data["auth_url"] == "https://accounts.google.com/auth?state=xyz"


async def test_auth_status_pending(auth_client: TestClient, auth_app: web.Application):
    db = auth_app["db"]
    await store.create_auth_session(
        db, "test_session", "identity", "test_state", "2099-01-01 00:00:00"
    )

    resp = await auth_client.get("/auth/status/test_session")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "pending"


async def test_auth_status_complete(auth_client: TestClient, auth_app: web.Application):
    db = auth_app["db"]
    await store.create_auth_session(
        db, "test_session", "identity", "test_state", "2099-01-01 00:00:00"
    )
    creds_json = '{"token": "access", "refresh_token": "refresh"}'
    await store.complete_identity_session(db, "test_session", "test@example.com", creds_json)

    resp = await auth_client.get("/auth/status/test_session")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "complete"
    assert data["credentials_json"] == creds_json
    assert data["email"] == "test@example.com"


async def test_auth_status_not_found(auth_client: TestClient):
    resp = await auth_client.get("/auth/status/nonexistent")
    assert resp.status == 404


async def test_auth_callback_missing_params(auth_client: TestClient):
    resp = await auth_client.get("/auth/callback")
    assert resp.status == 400


async def test_auth_callback_invalid_state(auth_client: TestClient):
    resp = await auth_client.get("/auth/callback?code=abc&state=invalid")
    assert resp.status == 400


async def test_auth_callback_success(auth_client: TestClient, auth_app: web.Application):
    db = auth_app["db"]
    await store.create_auth_session(db, "cb_session", "identity", "cb_state", "2099-01-01 00:00:00")

    mock_flow = MagicMock()
    mock_creds = MagicMock()
    mock_creds.id_token = {"email": "test@example.com"}
    mock_creds.token = "access_token"
    mock_creds.to_json.return_value = '{"token": "access_token"}'
    mock_flow.credentials = mock_creds

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = await auth_client.get("/auth/callback?code=authcode&state=cb_state")

    assert resp.status == 200
    text = await resp.text()
    assert "Authenticated" in text

    # Check session was completed with credentials
    session = await store.get_auth_session(db, "cb_session")
    assert session is not None
    assert session["email"] == "test@example.com"
    assert session["credentials_json"] == '{"token": "access_token"}'


async def test_auth_callback_non_owner(auth_client: TestClient, auth_app: web.Application):
    """Non-owner emails can still authenticate (AuthN allows anyone)."""
    db = auth_app["db"]
    await store.create_auth_session(
        db, "other_session", "identity", "other_state", "2099-01-01 00:00:00"
    )

    mock_flow = MagicMock()
    mock_creds = MagicMock()
    mock_creds.id_token = {"email": "other@example.com"}
    mock_creds.token = "access_token"
    mock_creds.to_json.return_value = '{"token": "access_token"}'
    mock_flow.credentials = mock_creds

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = await auth_client.get("/auth/callback?code=authcode&state=other_state")

    assert resp.status == 200
    text = await resp.text()
    assert "Authenticated" in text

    session = await store.get_auth_session(db, "other_session")
    assert session is not None
    assert session["email"] == "other@example.com"


# --- Service auth tests (calendar) ---


async def test_service_start_no_auth(auth_client: TestClient):
    resp = await auth_client.post("/auth/service/calendar/start")
    assert resp.status == 401


async def test_service_start_non_owner(auth_client: TestClient, auth_app: web.Application):
    """Non-owner is authenticated but forbidden from service delegation."""
    auth_app["http_session"] = MockHttpSession({"non_owner_token": (200, "other@example.com")})

    resp = await auth_client.post(
        "/auth/service/calendar/start",
        headers={"Authorization": "Bearer non_owner_token"},
    )
    assert resp.status == 403


async def test_service_start_unknown_service(auth_client: TestClient):
    resp = await auth_client.post(
        "/auth/service/unknown/start",
        headers={"Authorization": "Bearer owner_access_token"},
    )
    assert resp.status == 400


async def test_service_start_calendar(auth_client: TestClient, auth_app: web.Application):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/cal", "calstate")

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = await auth_client.post(
            "/auth/service/calendar/start",
            headers={"Authorization": "Bearer owner_access_token"},
        )

    assert resp.status == 200
    data = await resp.json()
    assert "session_id" in data
    assert "auth_url" in data


async def test_service_start_gmail(auth_client: TestClient, auth_app: web.Application):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/gmail", "gmstate")

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = await auth_client.post(
            "/auth/service/gmail/start",
            headers={"Authorization": "Bearer owner_access_token"},
        )

    assert resp.status == 200
    data = await resp.json()
    assert "session_id" in data
    assert "auth_url" in data


async def test_service_status_pending(auth_client: TestClient, auth_app: web.Application):
    db = auth_app["db"]
    await store.create_auth_session(
        db, "cal_session", "calendar", "cal_state", "2099-01-01 00:00:00"
    )

    resp = await auth_client.get("/auth/service/calendar/status/cal_session")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "pending"


async def test_service_status_not_found(auth_client: TestClient):
    resp = await auth_client.get("/auth/service/calendar/status/nonexistent")
    assert resp.status == 404


async def test_service_callback_calendar(auth_client: TestClient, auth_app: web.Application):
    db = auth_app["db"]
    await store.create_auth_session(
        db, "cal_cb_session", "calendar", "cal_cb_state", "2099-01-01 00:00:00"
    )

    mock_flow = MagicMock()
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "cal_token"}'
    mock_flow.credentials = mock_creds

    with (
        patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow),
        patch("homunculus.server.auth.make_calendar_tools", return_value=[]),
    ):
        resp = await auth_client.get(
            "/auth/service/calendar/callback?code=calcode&state=cal_cb_state"
        )

    assert resp.status == 200
    text = await resp.text()
    assert "Calendar access granted" in text

    # Check credentials stored
    creds_row = await store.get_google_credentials(db, "test@example.com", "calendar")
    assert creds_row is not None
    assert creds_row["credentials_json"] == '{"token": "cal_token"}'


async def test_service_callback_gmail(auth_client: TestClient, auth_app: web.Application):
    db = auth_app["db"]
    await store.create_auth_session(
        db, "gm_cb_session", "gmail", "gm_cb_state", "2099-01-01 00:00:00"
    )

    mock_flow = MagicMock()
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "gmail_token"}'
    mock_flow.credentials = mock_creds

    with (
        patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow),
        patch("homunculus.server.auth.make_gmail_tools", return_value=[]),
    ):
        resp = await auth_client.get("/auth/service/gmail/callback?code=gmcode&state=gm_cb_state")

    assert resp.status == 200
    text = await resp.text()
    assert "Gmail access granted" in text

    creds_row = await store.get_google_credentials(db, "test@example.com", "gmail")
    assert creds_row is not None
    assert creds_row["credentials_json"] == '{"token": "gmail_token"}'
