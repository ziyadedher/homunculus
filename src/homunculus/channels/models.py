from dataclasses import dataclass, field
from datetime import UTC, datetime

from homunculus.types import ChannelId, Contact, ConversationId, MessageId


@dataclass(frozen=True)
class Sender:
    identifier: str
    display_name: str | None = None


@dataclass(frozen=True)
class RawInboundMessage:
    """Message as received from a channel, before authentication."""

    sender: Sender
    body: str
    channel_id: ChannelId
    message_id: MessageId
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    conversation_id_override: ConversationId | None = None


@dataclass(frozen=True)
class InboundMessage:
    """Authenticated message with resolved contact."""

    sender: Sender
    body: str
    channel_id: ChannelId
    message_id: MessageId
    contact: Contact
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    conversation_id_override: ConversationId | None = None

    @property
    def conversation_id(self) -> ConversationId:
        if self.conversation_id_override is not None:
            return self.conversation_id_override
        return ConversationId(f"{self.channel_id}:{self.contact.contact_id}")


@dataclass(frozen=True)
class OutboundMessage:
    recipient_id: str
    body: str
    channel_id: ChannelId
