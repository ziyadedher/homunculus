from homunculus.channels.models import InboundMessage, RawInboundMessage, Sender
from homunculus.types import ChannelId, Contact, ContactId, MessageId


def test_raw_inbound_message_fields():
    msg = RawInboundMessage(
        sender=Sender(identifier="123456789"),
        body="hello",
        channel_id=ChannelId("telegram"),
        message_id=MessageId("test123"),
    )
    assert msg.sender.identifier == "123456789"
    assert msg.body == "hello"


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


def test_conversation_id_api_channel():
    contact = Contact(contact_id=ContactId("alice"), name="Alice")
    msg = InboundMessage(
        contact=contact,
        is_owner=True,
        body="hello",
        channel_id=ChannelId("api"),
        message_id=MessageId("test123"),
    )
    assert msg.conversation_id == "api:alice"


def test_sender_display_name():
    sender = Sender(identifier="123456789", display_name="Alice")
    assert sender.display_name == "Alice"

    sender2 = Sender(identifier="123456789")
    assert sender2.display_name is None
