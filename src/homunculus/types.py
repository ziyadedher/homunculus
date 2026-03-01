"""Shared domain types for the homunculus project."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import NewType

_TS_FMT = "%Y-%m-%d %H:%M:%S"

ApprovalId = NewType("ApprovalId", str)
ChannelId = NewType("ChannelId", str)
ContactId = NewType("ContactId", str)
ConversationId = NewType("ConversationId", str)
MessageId = NewType("MessageId", str)


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    AWAITING_APPROVAL = "awaiting_approval"


@dataclass(frozen=True)
class Message:
    """A single message in a conversation, wrapping the Anthropic API format with metadata."""

    role: str
    content: str | list[dict[str, object]]
    timestamp: datetime

    def to_api_param(self) -> dict[str, str | list[dict[str, object]]]:
        """Convert to the dict format expected by the Anthropic messages API."""
        return {"role": self.role, "content": self.content}

    def to_dict(self) -> dict[str, object]:
        """Serialize for JSON storage in the database."""
        return {
            "role": self.role,
            "content": self.content,
            "ts": self.timestamp.strftime(_TS_FMT),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Message:
        """Deserialize from JSON storage. Handles legacy messages without timestamps."""
        ts_raw = data.get("ts") or data.get("_ts")
        if isinstance(ts_raw, str):
            ts = datetime.strptime(ts_raw, _TS_FMT).replace(tzinfo=UTC)
        else:
            ts = datetime.now(UTC)
        content = data.get("content", "")
        if not isinstance(content, str | list):
            content = str(content)
        return cls(
            role=str(data.get("role", "unknown")),
            content=content,
            timestamp=ts,
        )

    @classmethod
    def user(cls, content: str | list[dict[str, object]]) -> Message:
        """Create a user message with the current timestamp."""
        return cls(role="user", content=content, timestamp=datetime.now(UTC))

    @classmethod
    def assistant(cls, content: str | list[dict[str, object]]) -> Message:
        """Create an assistant message with the current timestamp."""
        return cls(role="assistant", content=content, timestamp=datetime.now(UTC))
