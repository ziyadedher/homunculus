from homunculus.channels.models import InboundMessage
from homunculus.types import ChannelId, Contact, ContactId, MessageId


def test_conversation_id_from_contact():
    contact = Contact(contact_id=ContactId("alice"), name="Alice", telegram_chat_id="123456789")
    msg = InboundMessage(
        contact=contact,
        is_owner=False,
        body="hello",
        channel_id=ChannelId.TELEGRAM,
        message_id=MessageId("test123"),
    )
    assert msg.conversation_id == "telegram:alice"


def test_conversation_id_api_channel():
    contact = Contact(contact_id=ContactId("alice"), name="Alice")
    msg = InboundMessage(
        contact=contact,
        is_owner=True,
        body="hello",
        channel_id=ChannelId.API,
        message_id=MessageId("test123"),
    )
    assert msg.conversation_id == "api:alice"
