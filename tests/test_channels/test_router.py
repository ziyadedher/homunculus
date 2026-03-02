from unittest.mock import AsyncMock, patch

from homunculus.agent.loop import AgentResult
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.models import RawInboundMessage, Sender
from homunculus.channels.router import MessageRouter
from homunculus.storage import store
from homunculus.types import ChannelId, ContactId, MessageId


async def test_unauthorized_sender_rejected(db, config):
    """Non-contact, non-owner sender should get a rejection message."""
    registry = ToolRegistry()
    channel = AsyncMock()
    channel.channel_id = "telegram"

    router = MessageRouter(
        config=config, db=db, registry=registry, channels={ChannelId("telegram"): channel}
    )

    # No contacts in DB — sender is not authorized
    msg = RawInboundMessage(
        sender=Sender(identifier="999999999"),
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("msg3"),
    )

    await router.handle_inbound(msg)

    # Should have sent a rejection message
    assert channel.send.call_count == 1
    sent_msg = channel.send.call_args[0][0]
    assert "not authorized" in sent_msg.body.lower()


async def test_known_contact_gets_through(db, config):
    """A known contact should be processed by the agent."""
    registry = ToolRegistry()
    channel = AsyncMock()
    channel.channel_id = "telegram"

    router = MessageRouter(
        config=config, db=db, registry=registry, channels={ChannelId("telegram"): channel}
    )

    # Add contact
    await store.create_contact(db, ContactId("alice"), name="Alice", telegram_chat_id="111222333")

    msg = RawInboundMessage(
        sender=Sender(identifier="111222333"),
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("msg4"),
    )

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hi Alice!")
        await router.handle_inbound(msg)

        # Agent should have been called
        mock_agent.assert_called_once()
        # Contact should be passed
        call_kwargs = mock_agent.call_args.kwargs
        assert call_kwargs.get("contact") is not None
        assert call_kwargs["contact"].name == "Alice"


async def test_owner_message_gets_through(db, config):
    """Owner on Telegram should be authenticated even without a DB contact record."""
    registry = ToolRegistry()
    channel = AsyncMock()
    channel.channel_id = "telegram"

    router = MessageRouter(
        config=config, db=db, registry=registry, channels={ChannelId("telegram"): channel}
    )

    msg = RawInboundMessage(
        sender=Sender(identifier=config.owner.telegram_chat_id),
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("msg_owner"),
    )

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hi boss!")
        await router.handle_inbound(msg)

        mock_agent.assert_called_once()
        call_kwargs = mock_agent.call_args.kwargs
        # Owner gets a synthesized contact from config
        assert call_kwargs["contact"].name == config.owner.name
        assert call_kwargs["contact"].email == config.owner.email
