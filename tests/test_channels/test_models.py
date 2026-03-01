from homunculus.channels.models import InboundMessage, Sender
from homunculus.types import ChannelId, ContactId, MessageId


def test_conversation_id():
    msg = InboundMessage(
        sender=Sender(identifier="123456789"),
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("test123"),
    )
    assert msg.conversation_id == "telegram:123456789"


def test_conversation_id_with_contact_id():
    msg = InboundMessage(
        sender=Sender(identifier="123456789"),
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("test123"),
        contact_id=ContactId("abc123"),
    )
    assert msg.conversation_id == "telegram:abc123"


def test_conversation_id_without_contact_id():
    msg = InboundMessage(
        sender=Sender(identifier="123456789"),
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("test123"),
        contact_id=None,
    )
    assert msg.conversation_id == "telegram:123456789"


def test_sender_display_name():
    sender = Sender(identifier="123456789", display_name="Alice")
    assert sender.display_name == "Alice"

    sender2 = Sender(identifier="123456789")
    assert sender2.display_name is None
