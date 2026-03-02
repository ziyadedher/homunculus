from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from homunculus.storage import store
from homunculus.types import ChannelId, ConversationId, RequestStatus, RequestType


async def test_callback_query_approve(client: TestClient, api_app: tuple):
    """Telegram callback query with approve button resolves the request."""
    _app, state = api_app
    request_id = await store.create_request(
        state.db,
        ConversationId("telegram:alice123"),
        RequestType.APPROVAL,
        "Create lunch event",
        tool_name="create_event",
        tool_input={"summary": "Lunch"},
    )

    # Mock the TelegramChannel methods
    mock_channel = AsyncMock()
    mock_channel.channel_id = "telegram"
    mock_channel.answer_callback_query = AsyncMock()
    mock_channel.edit_message_text = AsyncMock()
    mock_channel.send_with_inline_keyboard = AsyncMock()
    mock_channel.send = AsyncMock()

    # Replace the channel in the router

    state.router._channels[ChannelId.TELEGRAM] = mock_channel

    update = {
        "callback_query": {
            "id": "callback123",
            "from": {"id": int(state.config.owner.telegram_chat_id)},
            "data": f"approve:{request_id}",
            "message": {
                "message_id": 456,
                "chat": {"id": int(state.config.owner.telegram_chat_id)},
                "text": "Approval needed: create_event",
            },
        }
    }

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AsyncMock(
            response_text="Event created!",
            request_message=None,
            request_id=None,
            resolved_request_ids=[],
        )
        resp = client.post(
            "/webhook/telegram",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": state.webhook_secret},
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify request was resolved
    req = await store.get_request(state.db, request_id)
    assert req is not None
    assert req.status in (RequestStatus.APPROVED, RequestStatus.COMPLETED)


async def test_callback_query_deny(client: TestClient, api_app: tuple):
    """Telegram callback query with deny button resolves the request."""
    _app, state = api_app
    request_id = await store.create_request(
        state.db,
        ConversationId("telegram:bob456"),
        RequestType.APPROVAL,
        "Delete event",
        tool_name="delete_event",
        tool_input={"event_id": "123"},
    )

    mock_channel = AsyncMock()
    mock_channel.channel_id = "telegram"
    mock_channel.answer_callback_query = AsyncMock()
    mock_channel.edit_message_text = AsyncMock()
    mock_channel.send_with_inline_keyboard = AsyncMock()
    mock_channel.send = AsyncMock()

    state.router._channels[ChannelId.TELEGRAM] = mock_channel

    update = {
        "callback_query": {
            "id": "callback456",
            "from": {"id": int(state.config.owner.telegram_chat_id)},
            "data": f"deny:{request_id}",
            "message": {
                "message_id": 789,
                "chat": {"id": int(state.config.owner.telegram_chat_id)},
                "text": "Approval needed: delete_event",
            },
        }
    }

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AsyncMock(
            response_text="Request denied.",
            request_message=None,
            request_id=None,
            resolved_request_ids=[],
        )
        resp = client.post(
            "/webhook/telegram",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": state.webhook_secret},
        )

    assert resp.status_code == 200

    req = await store.get_request(state.db, request_id)
    assert req is not None
    assert req.status in (RequestStatus.DENIED, RequestStatus.COMPLETED)


async def test_callback_query_non_owner_ignored(client: TestClient, api_app: tuple):
    """Callback queries from non-owner are silently ignored."""
    _app, state = api_app
    request_id = await store.create_request(
        state.db,
        ConversationId("telegram:alice123"),
        RequestType.APPROVAL,
        "Create event",
        tool_name="create_event",
        tool_input={"summary": "Test"},
    )

    update = {
        "callback_query": {
            "id": "callback789",
            "from": {"id": 999999},  # Not the owner
            "data": f"approve:{request_id}",
            "message": {
                "message_id": 111,
                "chat": {"id": 999999},
                "text": "Approval needed",
            },
        }
    }

    resp = client.post(
        "/webhook/telegram",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": state.webhook_secret},
    )

    assert resp.status_code == 200

    # Request should still be pending
    req = await store.get_request(state.db, request_id)
    assert req is not None
    assert req.status == RequestStatus.PENDING


async def test_callback_query_bad_secret(client: TestClient, api_app: tuple):
    """Callback query with wrong webhook secret is rejected."""
    resp = client.post(
        "/webhook/telegram",
        json={"callback_query": {"id": "cb1", "data": "approve:abc"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"},
    )
    assert resp.status_code == 403


async def test_callback_query_option(client: TestClient, api_app: tuple):
    """Telegram callback query with option button resolves the request."""
    _app, state = api_app
    request_id = await store.create_request(
        state.db,
        ConversationId("telegram:alice123"),
        RequestType.OPTIONS,
        "Pick a time",
        options=["9am", "10am", "11am"],
    )

    mock_channel = AsyncMock()
    mock_channel.channel_id = "telegram"
    mock_channel.answer_callback_query = AsyncMock()
    mock_channel.edit_message_text = AsyncMock()
    mock_channel.send_with_inline_keyboard = AsyncMock()
    mock_channel.send = AsyncMock()

    state.router._channels[ChannelId.TELEGRAM] = mock_channel

    update = {
        "callback_query": {
            "id": "callback_opt",
            "from": {"id": int(state.config.owner.telegram_chat_id)},
            "data": f"option:{request_id}:10am",
            "message": {
                "message_id": 999,
                "chat": {"id": int(state.config.owner.telegram_chat_id)},
                "text": "Pick a time",
            },
        }
    }

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AsyncMock(
            response_text="Got it, 10am!",
            request_message=None,
            request_id=None,
            resolved_request_ids=[],
        )
        resp = client.post(
            "/webhook/telegram",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": state.webhook_secret},
        )

    assert resp.status_code == 200

    req = await store.get_request(state.db, request_id)
    assert req is not None
    assert req.status in (RequestStatus.RESOLVED, RequestStatus.COMPLETED)
