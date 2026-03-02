from unittest.mock import AsyncMock, patch

from homunculus.agent.loop import AgentResult
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.models import InboundMessage
from homunculus.channels.router import MessageRouter
from homunculus.types import ChannelId, Contact, ContactId, MessageId


async def test_known_contact_gets_through(db, config):
    """An authenticated contact message should be processed by the agent."""
    registry = ToolRegistry()
    channel = AsyncMock()
    channel.channel_id = "telegram"

    router = MessageRouter(
        config=config, db=db, registry=registry, channels={ChannelId.TELEGRAM: channel}
    )

    contact = Contact(contact_id=ContactId("alice"), name="Alice", telegram_chat_id="111222333")

    msg = InboundMessage(
        contact=contact,
        is_owner=False,
        body="hello",
        channel_id=ChannelId.TELEGRAM,
        message_id=MessageId("msg4"),
    )

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hi Alice!")
        result = await router.handle_inbound(msg)

        mock_agent.assert_called_once()
        call_kwargs = mock_agent.call_args.kwargs
        assert call_kwargs["contact"].name == "Alice"
        assert call_kwargs["conversation_id"] == "telegram:alice"
        assert result.response_text == "Hi Alice!"


async def test_owner_message_gets_through(db, config):
    """Owner message should pass pending_requests to process_message."""
    registry = ToolRegistry()
    channel = AsyncMock()
    channel.channel_id = "telegram"

    router = MessageRouter(
        config=config, db=db, registry=registry, channels={ChannelId.TELEGRAM: channel}
    )

    contact = Contact(
        contact_id=ContactId("owner"),
        name=config.owner.name,
        email=config.owner.email,
        telegram_chat_id=config.owner.telegram_chat_id,
    )

    msg = InboundMessage(
        contact=contact,
        is_owner=True,
        body="hello",
        channel_id=ChannelId.TELEGRAM,
        message_id=MessageId("msg_owner"),
    )

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Hi boss!")
        await router.handle_inbound(msg)

        mock_agent.assert_called_once()
        call_kwargs = mock_agent.call_args.kwargs
        assert call_kwargs["contact"].name == config.owner.name
        assert call_kwargs["pending_requests"] is not None
