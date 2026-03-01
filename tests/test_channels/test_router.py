from unittest.mock import AsyncMock, patch

from homunculus.agent.loop import AgentResult
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.models import RawInboundMessage, Sender
from homunculus.channels.router import MessageRouter
from homunculus.storage import store
from homunculus.types import ApprovalStatus, ChannelId, ContactId, ConversationId, MessageId


async def test_owner_approval_yes(db, config):
    registry = ToolRegistry()
    channel = AsyncMock()
    channel.channel_id = "telegram"

    router = MessageRouter(
        config=config, db=db, registry=registry, channels={ChannelId("telegram"): channel}
    )

    # Create a contact so the conversation_id uses contact_id
    contact_id = await store.create_contact(
        db, ContactId("alice"), name="Alice", telegram_chat_id="111222333"
    )

    # Create a pending approval
    await store.create_approval(
        db,
        conversation_id=ConversationId(f"telegram:{contact_id}"),
        request_description="Create lunch event",
        tool_name="create_event",
        tool_input={
            "summary": "Lunch",
            "start": "2025-01-01T12:00:00",
            "end": "2025-01-01T13:00:00",
        },
    )

    # Owner says yes
    owner_msg = RawInboundMessage(
        sender=Sender(identifier=config.owner.telegram_chat_id),
        body="yes",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("msg1"),
    )

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Done! Lunch confirmed.")
        await router.handle_inbound(owner_msg)

        # process_message should be called with a resume message containing tool details
        mock_agent.assert_called_once()
        call_args = mock_agent.call_args
        resume_msg = call_args.kwargs.get("message_body") or call_args.args[0]
        assert "approved" in resume_msg.lower()
        assert "create_event" in resume_msg

    # Should have sent messages (response to requester + confirmation to owner)
    assert channel.send.call_count >= 1


async def test_owner_denial(db, config):
    registry = ToolRegistry()
    channel = AsyncMock()
    channel.channel_id = "telegram"

    router = MessageRouter(
        config=config, db=db, registry=registry, channels={ChannelId("telegram"): channel}
    )

    # Create a contact
    contact_id = await store.create_contact(
        db, ContactId("alice"), name="Alice", telegram_chat_id="111222333"
    )

    await store.create_approval(
        db,
        conversation_id=ConversationId(f"telegram:{contact_id}"),
        request_description="Create lunch event",
        tool_name="create_event",
        tool_input={"summary": "Lunch"},
    )

    owner_msg = RawInboundMessage(
        sender=Sender(identifier=config.owner.telegram_chat_id),
        body="no",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("msg2"),
    )

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Sorry, the request was denied.")
        await router.handle_inbound(owner_msg)

        # process_message should be called with a denial message
        mock_agent.assert_called_once()
        call_args = mock_agent.call_args
        resume_msg = call_args.kwargs.get("message_body") or call_args.args[0]
        assert "denied" in resume_msg.lower()

    # Should have sent messages
    assert channel.send.call_count >= 1


async def test_owner_approval_non_channel_conversation(db, config):
    """Approval for a non-telegram conversation should resolve and resume server-side."""
    registry = ToolRegistry()
    channel = AsyncMock()
    channel.channel_id = "telegram"

    router = MessageRouter(
        config=config, db=db, registry=registry, channels={ChannelId("telegram"): channel}
    )

    # Create a contact
    contact_id = await store.create_contact(
        db, ContactId("alice"), name="Alice", telegram_chat_id="111222333"
    )

    # Approval from a CLI-originated conversation (not telegram)
    await store.create_approval(
        db,
        conversation_id=ConversationId(f"cli:{contact_id}"),
        request_description="Create lunch event",
        tool_name="create_event",
        tool_input={"summary": "Lunch"},
    )

    owner_msg = RawInboundMessage(
        sender=Sender(identifier=config.owner.telegram_chat_id),
        body="yes",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("msg_cli_approval"),
    )

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Done! Lunch confirmed.")
        await router.handle_inbound(owner_msg)

        # process_message IS called (server now resumes all conversations)
        mock_agent.assert_called_once()

    # Should have sent messages (requester notification + owner confirmation)
    assert channel.send.call_count >= 1


async def test_approval_response_stored(db, config):
    """After approval resolution, the response_text should be stored in the approval record."""
    registry = ToolRegistry()
    channel = AsyncMock()
    channel.channel_id = "telegram"

    router = MessageRouter(
        config=config, db=db, registry=registry, channels={ChannelId("telegram"): channel}
    )

    contact_id = await store.create_contact(
        db, ContactId("alice"), name="Alice", telegram_chat_id="111222333"
    )

    approval_id = await store.create_approval(
        db,
        conversation_id=ConversationId(f"telegram:{contact_id}"),
        request_description="Create lunch event",
        tool_name="create_event",
        tool_input={"summary": "Lunch"},
    )

    owner_msg = RawInboundMessage(
        sender=Sender(identifier=config.owner.telegram_chat_id),
        body="yes",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("msg_stored"),
    )

    with patch("homunculus.channels.router.process_message") as mock_agent:
        mock_agent.return_value = AgentResult(response_text="Lunch event created!")
        await router.handle_inbound(owner_msg)

    # Check that response_text was stored and status is completed
    approval = await store.get_approval(db, approval_id)
    assert approval is not None
    assert approval.response_text == "Lunch event created!"
    assert approval.status == ApprovalStatus.COMPLETED


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
