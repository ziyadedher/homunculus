from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from homunculus.storage import store
from homunculus.types import ApprovalStatus, ChannelId, ConversationId


async def test_callback_query_approve(client: TestClient, api_app: tuple):
    """Telegram callback query with approve button resolves the approval."""
    _app, state = api_app
    approval_id = await store.create_approval(
        state.db,
        ConversationId("telegram:alice123"),
        "Create lunch event",
        "create_event",
        {"summary": "Lunch"},
    )

    # Mock the TelegramChannel methods
    mock_channel = AsyncMock()
    mock_channel.channel_id = "telegram"
    mock_channel.answer_callback_query = AsyncMock()
    mock_channel.edit_message_text = AsyncMock()
    mock_channel.send_with_inline_keyboard = AsyncMock()
    mock_channel.send = AsyncMock()

    # Replace the channel in the router

    state.router._channels[ChannelId("telegram")] = mock_channel

    update = {
        "callback_query": {
            "id": "callback123",
            "from": {"id": int(state.config.owner.telegram_chat_id)},
            "data": f"approve:{approval_id}",
            "message": {
                "message_id": 456,
                "chat": {"id": int(state.config.owner.telegram_chat_id)},
                "text": "Approval needed: create_event",
            },
        }
    }

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AsyncMock(
            response_text="Event created!", escalation_message=None, escalation_approval_id=None
        )
        resp = client.post(
            "/webhook/telegram",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": state.webhook_secret},
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify approval was resolved
    approval = await store.get_approval(state.db, approval_id)
    assert approval is not None
    assert approval.status in (ApprovalStatus.APPROVED, ApprovalStatus.COMPLETED)


async def test_callback_query_deny(client: TestClient, api_app: tuple):
    """Telegram callback query with deny button resolves the approval."""
    _app, state = api_app
    approval_id = await store.create_approval(
        state.db,
        ConversationId("telegram:bob456"),
        "Delete event",
        "delete_event",
        {"event_id": "123"},
    )

    mock_channel = AsyncMock()
    mock_channel.channel_id = "telegram"
    mock_channel.answer_callback_query = AsyncMock()
    mock_channel.edit_message_text = AsyncMock()
    mock_channel.send_with_inline_keyboard = AsyncMock()
    mock_channel.send = AsyncMock()

    state.router._channels[ChannelId("telegram")] = mock_channel

    update = {
        "callback_query": {
            "id": "callback456",
            "from": {"id": int(state.config.owner.telegram_chat_id)},
            "data": f"deny:{approval_id}",
            "message": {
                "message_id": 789,
                "chat": {"id": int(state.config.owner.telegram_chat_id)},
                "text": "Approval needed: delete_event",
            },
        }
    }

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AsyncMock(
            response_text="Request denied.", escalation_message=None, escalation_approval_id=None
        )
        resp = client.post(
            "/webhook/telegram",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": state.webhook_secret},
        )

    assert resp.status_code == 200

    approval = await store.get_approval(state.db, approval_id)
    assert approval is not None
    assert approval.status in (ApprovalStatus.DENIED, ApprovalStatus.COMPLETED)


async def test_callback_query_non_owner_ignored(client: TestClient, api_app: tuple):
    """Callback queries from non-owner are silently ignored."""
    _app, state = api_app
    approval_id = await store.create_approval(
        state.db,
        ConversationId("telegram:alice123"),
        "Create event",
        "create_event",
        {"summary": "Test"},
    )

    update = {
        "callback_query": {
            "id": "callback789",
            "from": {"id": 999999},  # Not the owner
            "data": f"approve:{approval_id}",
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

    # Approval should still be pending
    approval = await store.get_approval(state.db, approval_id)
    assert approval is not None
    assert approval.status == ApprovalStatus.PENDING


async def test_callback_query_bad_secret(client: TestClient, api_app: tuple):
    """Callback query with wrong webhook secret is rejected."""
    resp = client.post(
        "/webhook/telegram",
        json={"callback_query": {"id": "cb1", "data": "approve:abc"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"},
    )
    assert resp.status_code == 403
