from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from homunculus.agent.loop import AgentResult
from homunculus.server.dependencies import AppState, get_state
from homunculus.storage import store
from homunculus.types import ApprovalId, ApprovalStatus, ConversationId, ContactId

from .conftest import OWNER_EMAIL, VALID_TOKEN, MockHttpxTransport


async def test_health(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_api_message_no_auth(client: TestClient):
    resp = client.post("/api/message", json={"body": "hi"})
    assert resp.status_code == 401


async def test_api_message_bad_token(client: TestClient):
    resp = client.post(
        "/api/message",
        json={"body": "hi"},
        headers={"Authorization": "Bearer bad_token"},
    )
    assert resp.status_code == 401


async def test_api_message_success(client: TestClient):
    """Owner sends a message; client_id defaults to their email, conversation is api:owner."""
    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hello from agent!")
        resp = client.post(
            "/api/message",
            json={"body": "hello"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["response_text"] == "Hello from agent!"
    call_kwargs = mock_agent.call_args.kwargs
    assert call_kwargs["conversation_id"] == "api:owner"


async def test_api_message_override_client_id(client: TestClient, api_app: tuple):
    """Owner can override client_id to impersonate another identity."""
    _app, state = api_app
    await store.create_contact(state.db, ContactId("alice"), name="Alice")

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hi Alice!")
        resp = client.post(
            "/api/message",
            json={"body": "hello", "override_client_id": "alice"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 200
    call_kwargs = mock_agent.call_args.kwargs
    assert call_kwargs["conversation_id"] == "api:alice"


async def test_api_message_non_owner_cannot_override(client: TestClient, api_app: tuple):
    """Non-owner is forbidden from using override_client_id."""
    app, state = api_app
    new_http_client = httpx.AsyncClient(
        transport=MockHttpxTransport(
            {
                VALID_TOKEN: (200, OWNER_EMAIL),
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

    resp = client.post(
        "/api/message",
        json={"body": "hi", "override_client_id": "alice"},
        headers={"Authorization": "Bearer other_token"},
    )

    assert resp.status_code == 403
    await new_http_client.aclose()


async def test_api_message_non_owner_default_client_id(client: TestClient, api_app: tuple):
    """Non-owner can send messages; client_id defaults to their email."""
    app, state = api_app
    await store.create_contact(
        state.db, ContactId("other"), name="Other", email="other@example.com"
    )

    new_http_client = httpx.AsyncClient(
        transport=MockHttpxTransport(
            {
                VALID_TOKEN: (200, OWNER_EMAIL),
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

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hello!")
        resp = client.post(
            "/api/message",
            json={"body": "hi"},
            headers={"Authorization": "Bearer other_token"},
        )

    assert resp.status_code == 200
    call_kwargs = mock_agent.call_args.kwargs
    assert call_kwargs["conversation_id"] == "api:other"
    await new_http_client.aclose()


async def test_api_message_missing_body(client: TestClient):
    resp = client.post(
        "/api/message",
        json={},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status_code == 422  # Pydantic validation error


async def test_api_message_with_escalation(client: TestClient, api_app: tuple):
    _app, state = api_app
    await store.create_contact(state.db, ContactId("alice"), name="Alice")

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(
            response_text="Checking with owner...",
            escalation_message="Approval needed: create_event",
            escalation_approval_id=ApprovalId("abc123"),
        )
        resp = client.post(
            "/api/message",
            json={"body": "create event", "override_client_id": "alice"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["response_text"] == "Checking with owner..."
    assert data["escalation_message"] == "Approval needed: create_event"
    assert data["approval_id"] == "abc123"


async def test_api_get_approval_not_found(client: TestClient):
    resp = client.get(
        "/api/approvals/nonexistent",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status_code == 404


async def test_api_get_approval_pending(client: TestClient, api_app: tuple):
    _app, state = api_app
    approval_id = await store.create_approval(
        state.db,
        ConversationId("api:alice"),
        "Create lunch",
        "create_event",
        {"summary": "Lunch"},
    )

    resp = client.get(
        f"/api/approvals/{approval_id}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert data["response_text"] is None


async def test_api_get_approval_resolved_with_response(client: TestClient, api_app: tuple):
    _app, state = api_app
    approval_id = await store.create_approval(
        state.db,
        ConversationId("api:alice"),
        "Create lunch",
        "create_event",
        {"summary": "Lunch"},
    )
    await store.resolve_approval(state.db, approval_id, ApprovalStatus.APPROVED)
    await store.save_approval_response(state.db, approval_id, "Lunch event created!")
    await store.complete_approval(state.db, approval_id)

    resp = client.get(
        f"/api/approvals/{approval_id}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["response_text"] == "Lunch event created!"


async def test_api_get_approval_no_auth(client: TestClient):
    resp = client.get("/api/approvals/some_id")
    assert resp.status_code == 401


async def test_api_get_approval_non_owner(client: TestClient, api_app: tuple):
    """Non-owner is authenticated but forbidden from polling approvals."""
    app, state = api_app
    new_http_client = httpx.AsyncClient(
        transport=MockHttpxTransport({"other_token": (200, "other@example.com")})
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

    resp = client.get(
        "/api/approvals/some_id",
        headers={"Authorization": "Bearer other_token"},
    )
    assert resp.status_code == 403
    await new_http_client.aclose()
