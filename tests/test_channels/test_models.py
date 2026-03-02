from homunculus.channels.models import InboundMessage, RawInboundMessage, Sender
from homunculus.types import ChannelId, Contact, ContactId, ConversationId, MessageId


def test_raw_inbound_message_has_no_conversation_id():
    msg = RawInboundMessage(
        sender=Sender(identifier="123456789"),
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("test123"),
    )
    assert msg.sender.identifier == "123456789"
    assert msg.body == "hello"
    assert msg.conversation_id_override is None


def test_raw_inbound_message_with_override():
    msg = RawInboundMessage(
        sender=Sender(identifier="123456789"),
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("test123"),
        conversation_id_override=ConversationId("api:custom"),
    )
    assert msg.conversation_id_override == "api:custom"


def test_conversation_id_from_contact():
    contact = Contact(contact_id=ContactId("alice"), name="Alice", telegram_chat_id="123456789")
    msg = InboundMessage(
        contact=contact,
        is_owner=False,
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("test123"),
    )
    assert msg.conversation_id == "telegram:alice"


def test_conversation_id_override():
    contact = Contact(contact_id=ContactId("alice"), name="Alice")
    msg = InboundMessage(
        contact=contact,
        is_owner=True,
        body="hello",
        channel_id=ChannelId("api"),
        message_id=MessageId("test123"),
        conversation_id_override=ConversationId("telegram:alice"),
    )
    assert msg.conversation_id == "telegram:alice"


def test_sender_display_name():
    sender = Sender(identifier="123456789", display_name="Alice")
    assert sender.display_name == "Alice"

    sender2 = Sender(identifier="123456789")
    assert sender2.display_name is None
