from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials

from homunculus.server.dependencies import AppState, get_state
from homunculus.storage import store

from .conftest import OWNER_EMAIL, MockHttpxTransport


async def test_auth_start(auth_client: TestClient):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/auth?state=xyz", "xyz")
    mock_flow.code_verifier = "test_verifier"

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = auth_client.post("/auth/start")

    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert "auth_url" in data
    assert data["auth_url"] == "https://accounts.google.com/auth?state=xyz"


async def test_auth_status_pending(auth_client: TestClient, auth_app: tuple):
    _app, state = auth_app
    await store.create_auth_session(
        state.db, "test_session", "identity", "test_state", "2099-01-01 00:00:00"
    )

    resp = auth_client.get("/auth/status/test_session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"


async def test_auth_status_complete(auth_client: TestClient, auth_app: tuple):
    _app, state = auth_app
    await store.create_auth_session(
        state.db, "test_session", "identity", "test_state", "2099-01-01 00:00:00"
    )
    creds_json = '{"token": "access", "refresh_token": "refresh"}'
    await store.complete_identity_session(state.db, "test_session", "test@example.com", creds_json)

    resp = auth_client.get("/auth/status/test_session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "complete"
    assert data["credentials_json"] == creds_json
    assert data["email"] == "test@example.com"


async def test_auth_status_not_found(auth_client: TestClient):
    resp = auth_client.get("/auth/status/nonexistent")
    assert resp.status_code == 404


async def test_auth_callback_missing_params(auth_client: TestClient):
    resp = auth_client.get("/auth/callback")
    assert resp.status_code == 400


async def test_auth_callback_invalid_state(auth_client: TestClient):
    resp = auth_client.get("/auth/callback?code=abc&state=invalid")
    assert resp.status_code == 400


async def test_auth_callback_success(auth_client: TestClient, auth_app: tuple):
    _app, state = auth_app
    await store.create_auth_session(
        state.db, "cb_session", "identity", "cb_state", "2099-01-01 00:00:00"
    )

    mock_flow = MagicMock()
    mock_creds = MagicMock(spec=Credentials)
    mock_creds.id_token = {"email": "test@example.com"}
    mock_creds.token = "access_token"
    mock_creds.to_json.return_value = '{"token": "access_token"}'
    mock_flow.credentials = mock_creds

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = auth_client.get("/auth/callback?code=authcode&state=cb_state")

    assert resp.status_code == 200
    text = resp.text
    assert "Authenticated" in text

    # Check session was completed with credentials
    session = await store.get_auth_session(state.db, "cb_session")
    assert session is not None
    assert session["email"] == "test@example.com"
    assert session["credentials_json"] == '{"token": "access_token"}'


async def test_auth_callback_non_owner(auth_client: TestClient, auth_app: tuple):
    """Non-owner emails can still authenticate (AuthN allows anyone)."""
    _app, state = auth_app
    await store.create_auth_session(
        state.db, "other_session", "identity", "other_state", "2099-01-01 00:00:00"
    )

    mock_flow = MagicMock()
    mock_creds = MagicMock(spec=Credentials)
    mock_creds.id_token = {"email": "other@example.com"}
    mock_creds.token = "access_token"
    mock_creds.to_json.return_value = '{"token": "access_token"}'
    mock_flow.credentials = mock_creds

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = auth_client.get("/auth/callback?code=authcode&state=other_state")

    assert resp.status_code == 200
    text = resp.text
    assert "Authenticated" in text

    session = await store.get_auth_session(state.db, "other_session")
    assert session is not None
    assert session["email"] == "other@example.com"


# --- Whoami ---


async def test_whoami_unauthenticated(auth_client: TestClient):
    resp = auth_client.get("/auth/whoami")
    assert resp.status_code == 401


async def test_whoami_owner(auth_client: TestClient, auth_app: tuple):
    _app, state = auth_app
    # Grant calendar service
    await store.save_google_credentials(
        state.db, "test@example.com", "calendar", '{"token": "cal"}', "calendar_scope"
    )

    resp = auth_client.get("/auth/whoami", headers={"Authorization": "Bearer owner_access_token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "test@example.com"
    assert data["is_owner"] is True
    assert "calendar" in data["services"]


async def test_whoami_non_owner(auth_client: TestClient, auth_app: tuple):
    app, state = auth_app
    new_http_client = httpx.AsyncClient(
        transport=MockHttpxTransport(
            {
                "owner_access_token": (200, OWNER_EMAIL),
                "other_token": (200, "other@example.com"),
            }
        )
    )
    new_state = AppState(
        config=state.config,
        db=state.db,
        registry=state.registry,
        router=state.router,
        http_client=new_http_client,
        webhook_secret=state.webhook_secret,
    )
    app.dependency_overrides[get_state] = lambda: new_state

    resp = auth_client.get("/auth/whoami", headers={"Authorization": "Bearer other_token"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "other@example.com"
    assert data["is_owner"] is False
    assert data["services"] == []
    await new_http_client.aclose()


# --- Service auth tests ---


async def test_service_start_no_auth(auth_client: TestClient):
    resp = auth_client.post("/auth/service/calendar/start")
    assert resp.status_code == 401


async def test_service_start_non_owner(auth_client: TestClient, auth_app: tuple):
    """Non-owner is authenticated but forbidden from service delegation."""
    app, state = auth_app
    new_http_client = httpx.AsyncClient(
        transport=MockHttpxTransport({"non_owner_token": (200, "other@example.com")})
    )
    new_state = AppState(
        config=state.config,
        db=state.db,
        registry=state.registry,
        router=state.router,
        http_client=new_http_client,
        webhook_secret=state.webhook_secret,
    )
    app.dependency_overrides[get_state] = lambda: new_state

    resp = auth_client.post(
        "/auth/service/calendar/start",
        headers={"Authorization": "Bearer non_owner_token"},
    )
    assert resp.status_code == 403
    await new_http_client.aclose()


async def test_service_start_unknown_service(auth_client: TestClient):
    resp = auth_client.post(
        "/auth/service/unknown/start",
        headers={"Authorization": "Bearer owner_access_token"},
    )
    assert resp.status_code == 400


async def test_service_start_calendar(auth_client: TestClient):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/cal", "calstate")
    mock_flow.code_verifier = "test_verifier"

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = auth_client.post(
            "/auth/service/calendar/start",
            headers={"Authorization": "Bearer owner_access_token"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert "auth_url" in data


async def test_service_start_email(auth_client: TestClient):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = ("https://accounts.google.com/email", "emstate")
    mock_flow.code_verifier = "test_verifier"

    with patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow):
        resp = auth_client.post(
            "/auth/service/email/start",
            headers={"Authorization": "Bearer owner_access_token"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert "auth_url" in data


async def test_service_status_pending(auth_client: TestClient, auth_app: tuple):
    _app, state = auth_app
    await store.create_auth_session(
        state.db, "cal_session", "calendar", "cal_state", "2099-01-01 00:00:00"
    )

    resp = auth_client.get("/auth/service/calendar/status/cal_session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"


async def test_service_status_not_found(auth_client: TestClient):
    resp = auth_client.get("/auth/service/calendar/status/nonexistent")
    assert resp.status_code == 404


async def test_service_callback_calendar(auth_client: TestClient, auth_app: tuple):
    """Service callback now goes through unified /auth/callback."""
    _app, state = auth_app
    await store.create_auth_session(
        state.db, "cal_cb_session", "calendar", "cal_cb_state", "2099-01-01 00:00:00"
    )

    mock_flow = MagicMock()
    mock_creds = MagicMock(spec=Credentials)
    mock_creds.to_json.return_value = '{"token": "cal_token"}'
    mock_flow.credentials = mock_creds

    with (
        patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow),
        patch("homunculus.server.auth.make_calendar_tools", return_value=[]),
    ):
        resp = auth_client.get("/auth/callback?code=calcode&state=cal_cb_state")

    assert resp.status_code == 200
    text = resp.text
    assert "Calendar access granted" in text

    # Check credentials stored
    creds_row = await store.get_google_credentials(state.db, "test@example.com", "calendar")
    assert creds_row is not None
    assert creds_row["credentials_json"] == '{"token": "cal_token"}'


async def test_service_callback_email(auth_client: TestClient, auth_app: tuple):
    """Service callback now goes through unified /auth/callback."""
    _app, state = auth_app
    await store.create_auth_session(
        state.db, "em_cb_session", "email", "em_cb_state", "2099-01-01 00:00:00"
    )

    mock_flow = MagicMock()
    mock_creds = MagicMock(spec=Credentials)
    mock_creds.to_json.return_value = '{"token": "email_token"}'
    mock_flow.credentials = mock_creds

    with (
        patch("homunculus.server.auth.Flow.from_client_secrets_file", return_value=mock_flow),
        patch("homunculus.server.auth.make_email_tools", return_value=[]),
    ):
        resp = auth_client.get("/auth/callback?code=emcode&state=em_cb_state")

    assert resp.status_code == 200
    text = resp.text
    assert "Email access granted" in text

    creds_row = await store.get_google_credentials(state.db, "test@example.com", "email")
    assert creds_row is not None
    assert creds_row["credentials_json"] == '{"token": "email_token"}'
