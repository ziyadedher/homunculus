from dataclasses import dataclass, field
from datetime import UTC, datetime

from homunculus.types import ChannelId, Contact, ConversationId, MessageId


@dataclass(frozen=True)
class InboundMessage:
    """Authenticated message with resolved identity.

    Constructed by each handler after authenticating the sender and resolving
    a Contact from the database. Messages that cannot be resolved to a Contact
    are rejected at the handler boundary.
    """

    contact: Contact
    is_owner: bool
    body: str
    channel_id: ChannelId
    message_id: MessageId
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def conversation_id(self) -> ConversationId:
        return ConversationId(f"{self.channel_id}:{self.contact.contact_id}")


@dataclass(frozen=True)
class OutboundMessage:
    recipient_id: str
    body: str
    channel_id: ChannelId
