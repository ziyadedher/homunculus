from homunculus.channels.models import InboundMessage, Sender
from homunculus.types import ChannelId, ContactId, MessageId


def test_conversation_id():
    msg = InboundMessage(
        sender=Sender(phone="+11234567890"),
        body="hello",
        channel_id=ChannelId("sms"),
        message_id=MessageId("test123"),
    )
    assert msg.conversation_id == "sms:+11234567890"


def test_conversation_id_with_contact_id():
    msg = InboundMessage(
        sender=Sender(phone="+11234567890"),
        body="hello",
        channel_id=ChannelId("sms"),
        message_id=MessageId("test123"),
        contact_id=ContactId("abc123"),
    )
    assert msg.conversation_id == "sms:abc123"


def test_conversation_id_without_contact_id():
    msg = InboundMessage(
        sender=Sender(phone="+11234567890"),
        body="hello",
        channel_id=ChannelId("sms"),
        message_id=MessageId("test123"),
        contact_id=None,
    )
    assert msg.conversation_id == "sms:+11234567890"


def test_sender_display_name():
    sender = Sender(phone="+11234567890", display_name="Alice")
    assert sender.display_name == "Alice"

    sender2 = Sender(phone="+11234567890")
    assert sender2.display_name is None
