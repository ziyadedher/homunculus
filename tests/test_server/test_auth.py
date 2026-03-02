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
    handle_auth_whoami,
    handle_service_start,
    handle_service_status,
)
from homunculus.storage import store
from homunculus.storage.store import open_store
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
            email=GoogleEmailConfig(),
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
    app.router.add_get("/auth/whoami", handle_auth_whoami)
    app.router.add_post("/auth/service/{service}/start", handle_service_start)
    app.router.add_get("/auth/service/{service}/status/{session_id}", handle_service_status)

    yield app

    await db.close()


@pytest.fixture
async def auth_client(auth_app: web.Application, aiohttp_client) -> TestClient:
    return await aiohttp_client(auth_app)


async def test_auth_start(auth_client: TestClient):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/auth?state=xyz", "xyz")
    mock_flow.code_verifier = "test_verifier"

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


# --- Whoami ---


async def test_whoami_unauthenticated(auth_client: TestClient):
    resp = await auth_client.get("/auth/whoami")
    assert resp.status == 401


async def test_whoami_owner(auth_client: TestClient, auth_app: web.Application):
    db = auth_app["db"]
    # Grant calendar service
    await store.save_google_credentials(
        db, "test@example.com", "calendar", '{"token": "cal"}', "calendar_scope"
    )

    resp = await auth_client.get(
        "/auth/whoami", headers={"Authorization": "Bearer owner_access_token"}
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["email"] == "test@example.com"
    assert data["is_owner"] is True
    assert "calendar" in data["services"]


async def test_whoami_non_owner(auth_client: TestClient, auth_app: web.Application):
    auth_app["http_session"] = MockHttpSession({"other_token": (200, "other@example.com")})

    resp = await auth_client.get("/auth/whoami", headers={"Authorization": "Bearer other_token"})
    assert resp.status == 200
    data = await resp.json()
    assert data["email"] == "other@example.com"
    assert data["is_owner"] is False
    assert data["services"] == []


# --- Service auth tests ---


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
    mock_flow.code_verifier = "test_verifier"

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = await auth_client.post(
            "/auth/service/calendar/start",
            headers={"Authorization": "Bearer owner_access_token"},
        )

    assert resp.status == 200
    data = await resp.json()
    assert "session_id" in data
    assert "auth_url" in data


async def test_service_start_email(auth_client: TestClient, auth_app: web.Application):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/email", "emstate")
    mock_flow.code_verifier = "test_verifier"

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = await auth_client.post(
            "/auth/service/email/start",
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
    """Service callback now goes through unified /auth/callback."""
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
        resp = await auth_client.get("/auth/callback?code=calcode&state=cal_cb_state")

    assert resp.status == 200
    text = await resp.text()
    assert "Calendar access granted" in text

    # Check credentials stored
    creds_row = await store.get_google_credentials(db, "test@example.com", "calendar")
    assert creds_row is not None
    assert creds_row["credentials_json"] == '{"token": "cal_token"}'


async def test_service_callback_email(auth_client: TestClient, auth_app: web.Application):
    """Service callback now goes through unified /auth/callback."""
    db = auth_app["db"]
    await store.create_auth_session(
        db, "em_cb_session", "email", "em_cb_state", "2099-01-01 00:00:00"
    )

    mock_flow = MagicMock()
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "email_token"}'
    mock_flow.credentials = mock_creds

    with (
        patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow),
        patch("homunculus.server.auth.make_email_tools", return_value=[]),
    ):
        resp = await auth_client.get("/auth/callback?code=emcode&state=em_cb_state")

    assert resp.status == 200
    text = await resp.text()
    assert "Email access granted" in text

    creds_row = await store.get_google_credentials(db, "test@example.com", "email")
    assert creds_row is not None
    assert creds_row["credentials_json"] == '{"token": "email_token"}'
