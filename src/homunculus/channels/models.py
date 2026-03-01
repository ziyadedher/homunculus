from dataclasses import dataclass, field
from datetime import UTC, datetime

from homunculus.types import ChannelId, ContactId, ConversationId, MessageId


@dataclass(frozen=True)
class Sender:
    identifier: str
    display_name: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    sender: Sender
    body: str
    channel_id: ChannelId
    message_id: MessageId
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    contact_id: ContactId | None = None

    @property
    def conversation_id(self) -> ConversationId:
        if self.contact_id is not None:
            return ConversationId(f"{self.channel_id}:{self.contact_id}")
        return ConversationId(f"{self.channel_id}:{self.sender.identifier}")


@dataclass(frozen=True)
class OutboundMessage:
    recipient_id: str
    body: str
    channel_id: ChannelId
