from unittest.mock import patch

from fastapi.testclient import TestClient

from homunculus.agent.loop import AgentResult
from homunculus.storage import store
from homunculus.types import ContactId, ConversationId, RequestId, RequestStatus, RequestType

from .conftest import NON_OWNER_EMAIL, NON_OWNER_TOKEN, VALID_TOKEN


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


async def test_api_message_success(client: TestClient, api_app: tuple):
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


async def test_api_message_override_missing_contact(client: TestClient):
    """Owner override with nonexistent contact_id returns 404."""
    resp = client.post(
        "/api/message",
        json={"body": "hello", "override_client_id": "nonexistent"},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status_code == 404


async def test_api_message_non_owner_cannot_override(client: TestClient, api_app: tuple):
    """Non-owner is forbidden from using override_client_id."""
    _app, state = api_app
    await store.create_contact(state.db, ContactId("other"), name="Other", email=NON_OWNER_EMAIL)

    resp = client.post(
        "/api/message",
        json={"body": "hi", "override_client_id": "alice"},
        headers={"Authorization": f"Bearer {NON_OWNER_TOKEN}"},
    )
    assert resp.status_code == 403


async def test_api_message_non_owner_default_client_id(client: TestClient, api_app: tuple):
    """Non-owner can send messages; client_id defaults to their email."""
    _app, state = api_app
    await store.create_contact(state.db, ContactId("other"), name="Other", email=NON_OWNER_EMAIL)

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hello!")
        resp = client.post(
            "/api/message",
            json={"body": "hi"},
            headers={"Authorization": f"Bearer {NON_OWNER_TOKEN}"},
        )

    assert resp.status_code == 200
    call_kwargs = mock_agent.call_args.kwargs
    assert call_kwargs["conversation_id"] == "api:other"


async def test_api_message_no_contact_for_email(client: TestClient):
    """Non-owner with valid token but no contact in DB returns 403."""
    resp = client.post(
        "/api/message",
        json={"body": "hi"},
        headers={"Authorization": f"Bearer {NON_OWNER_TOKEN}"},
    )
    assert resp.status_code == 403


async def test_api_message_missing_body(client: TestClient):
    resp = client.post(
        "/api/message",
        json={},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status_code == 422  # Pydantic validation error


async def test_api_message_with_request(client: TestClient, api_app: tuple):
    _app, state = api_app
    await store.create_contact(state.db, ContactId("alice"), name="Alice")

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(
            response_text="Checking with owner...",
            request_message="Approval needed: create_event",
            request_id=RequestId("abc123"),
        )
        resp = client.post(
            "/api/message",
            json={"body": "create event", "override_client_id": "alice"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["response_text"] == "Checking with owner..."
    assert data["request_message"] == "Approval needed: create_event"
    assert data["request_id"] == "abc123"


async def test_api_get_request_not_found(client: TestClient):
    resp = client.get(
        "/api/requests/nonexistent",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status_code == 404


async def test_api_get_request_pending(client: TestClient, api_app: tuple):
    _app, state = api_app
    request_id = await store.create_request(
        state.db,
        ConversationId("api:alice"),
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"summary": "Lunch"},
    )

    resp = client.get(
        f"/api/requests/{request_id}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert data["response_text"] is None


async def test_api_get_request_resolved_with_response(client: TestClient, api_app: tuple):
    _app, state = api_app
    request_id = await store.create_request(
        state.db,
        ConversationId("api:alice"),
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"summary": "Lunch"},
    )
    await store.resolve_request(state.db, request_id, RequestStatus.APPROVED)
    await store.save_request_response(state.db, request_id, "Lunch event created!")
    await store.complete_request(state.db, request_id)

    resp = client.get(
        f"/api/requests/{request_id}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["response_text"] == "Lunch event created!"


async def test_api_get_request_no_auth(client: TestClient):
    resp = client.get("/api/requests/some_id")
    assert resp.status_code == 401


async def test_api_get_request_non_owner(client: TestClient):
    """Non-owner is authenticated but forbidden from polling requests."""
    resp = client.get(
        "/api/requests/some_id",
        headers={"Authorization": f"Bearer {NON_OWNER_TOKEN}"},
    )
    assert resp.status_code == 403
