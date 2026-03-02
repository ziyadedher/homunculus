import json

import aiosqlite

from homunculus.agent.loop import AgentResult, process_message
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.base import Channel
from homunculus.channels.models import InboundMessage, OutboundMessage, RawInboundMessage
from homunculus.storage import store
from homunculus.types import (
    Approval,
    ApprovalStatus,
    ChannelId,
    Contact,
    ContactId,
)
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger
from homunculus.utils.tracing import get_tracer

log = get_logger()
tracer = get_tracer(__name__)


class MessageRouter:
    def __init__(
        self,
        config: ServeConfig,
        db: aiosqlite.Connection,
        registry: ToolRegistry,
        channels: dict[ChannelId, Channel],
    ) -> None:
        self._config = config
        self._db = db
        self._registry = registry
        self._channels = channels

    async def handle_inbound(self, raw: RawInboundMessage) -> AgentResult | None:
        """Route an inbound message through auth, agent processing, and escalation.

        Returns AgentResult if the message was processed by the agent, or None if
        handled internally (approval reply) or rejected (unauthorized sender).
        Callers are responsible for delivering the response to the original sender.
        """
        # Check if this is the owner responding to a pending approval (Telegram only)
        if raw.channel_id == ChannelId("telegram") and self._is_owner(raw):
            handled = await self._handle_owner_reply(raw)
            if handled:
                return None

        # Resolve contact
        contact = await self._resolve_contact(raw)

        # Reject unknown senders on Telegram
        if contact is None and raw.channel_id == ChannelId("telegram"):
            log.info("unauthorized_sender", identifier=raw.sender.identifier)
            await self._send_via_channel(
                ChannelId("telegram"),
                raw.sender.identifier,
                "Sorry, you are not authorized to use this service.",
            )
            return None

        # Construct authenticated message for conversation_id computation
        if contact is not None:
            message = InboundMessage(
                sender=raw.sender,
                body=raw.body,
                channel_id=raw.channel_id,
                message_id=raw.message_id,
                contact=contact,
                timestamp=raw.timestamp,
                conversation_id_override=raw.conversation_id_override,
            )
            conversation_id = message.conversation_id
        elif raw.conversation_id_override is not None:
            conversation_id = raw.conversation_id_override
        else:
            return None

        await store.log_action(
            self._db,
            action_type="inbound_message",
            conversation_id=conversation_id,
            details={"sender": raw.sender.identifier, "body": raw.body},
        )

        # Regular message — send to agent
        result = await process_message(
            message_body=raw.body,
            conversation_id=conversation_id,
            config=self._config,
            db=self._db,
            registry=self._registry,
            contact=contact,
        )

        # If escalation happened, notify the owner
        if result.escalation_message:
            await self._notify_owner(result.escalation_message)

        return result

    def _is_owner(self, raw: RawInboundMessage) -> bool:
        if raw.channel_id == ChannelId("telegram"):
            return raw.sender.identifier == self._config.owner.telegram_chat_id
        return raw.channel_id == ChannelId("api")  # pre-authenticated via Google OAuth

    async def _resolve_contact(self, raw: RawInboundMessage) -> Contact | None:
        if raw.channel_id == ChannelId("telegram"):
            return await store.get_contact_by_telegram_chat_id(self._db, raw.sender.identifier)
        if (
            raw.channel_id == ChannelId("api")
            and raw.conversation_id_override is not None
            and ":" in raw.conversation_id_override
        ):
            identifier = raw.conversation_id_override.split(":", 1)[1]
            contact = await store.get_contact(self._db, ContactId(identifier))
            if contact is not None:
                return contact
            contact = await store.get_contact_by_telegram_chat_id(self._db, identifier)
            if contact is not None:
                return contact
            contact = await store.get_contact_by_phone(self._db, identifier)
            if contact is not None:
                return contact
            return await store.get_contact_by_email(self._db, identifier)
        return None

    async def _notify_owner(self, body: str) -> None:
        """Send a notification to the owner via Telegram (if available)."""
        try:
            await self._send_via_channel(
                ChannelId("telegram"),
                self._config.owner.telegram_chat_id,
                body,
            )
        except Exception:
            log.warning("notify_owner_failed")

    async def _send_via_channel(self, channel_id: ChannelId, recipient_id: str, body: str) -> None:
        channel = self._channels.get(channel_id)
        if channel is not None:
            await channel.send(
                OutboundMessage(
                    recipient_id=recipient_id,
                    body=body,
                    channel_id=channel_id,
                )
            )

    async def _handle_owner_reply(self, raw: RawInboundMessage) -> bool:
        approval = await store.get_oldest_pending_approval(self._db)
        if approval is None:
            return False  # No pending approvals — treat as regular message

        body_lower = raw.body.strip().lower()
        approved = body_lower in ("yes", "y", "approve", "ok", "sure", "yep", "yeah")
        denied = body_lower in ("no", "n", "deny", "nope", "nah", "cancel")

        if not approved and not denied:
            return False  # Ambiguous response — treat as regular message

        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
        await store.resolve_approval(self._db, approval.id, status)
        await store.log_action(
            self._db,
            action_type=f"approval_{status}",
            conversation_id=approval.conversation_id,
            details={"approval_id": approval.id},
        )

        with tracer.start_as_current_span("escalation.owner_reply") as span:
            span.set_attribute("approval.id", str(approval.id))
            span.set_attribute("approval.status", str(status))

            await self._resume_after_approval(approval, approved)

        return True

    async def _resume_after_approval(self, approval: Approval, approved: bool) -> None:
        if approved:
            resume_body = (
                f"Owner approved request {approval.id}. "
                f"The approved action is: {approval.tool_name}({json.dumps(approval.tool_input)}). "
                f"Please execute it now."
            )
        else:
            resume_body = (
                f"Owner denied request {approval.id}. "
                f"Inform the requester that the request was denied."
            )

        # Look up contact from conversation_id (format: channel:contact_id)
        contact_id_str = approval.conversation_id.split(":", 1)[1]
        contact = await store.get_contact(self._db, ContactId(contact_id_str))

        agent_result = await process_message(
            message_body=resume_body,
            conversation_id=approval.conversation_id,
            config=self._config,
            db=self._db,
            registry=self._registry,
            contact=contact,
            approved_tools={approval.tool_name} if approved and approval.tool_name else None,
        )

        # Store the response and mark approval as completed
        if agent_result.response_text:
            await store.save_approval_response(self._db, approval.id, agent_result.response_text)
        await store.complete_approval(self._db, approval.id)

        # Best-effort send to the original requester via Telegram
        if (
            contact is not None
            and contact.telegram_chat_id is not None
            and agent_result.response_text
        ):
            try:
                await self._send_via_channel(
                    ChannelId("telegram"),
                    contact.telegram_chat_id,
                    agent_result.response_text,
                )
            except Exception:
                log.warning("send_to_requester_failed", requester_id=contact.telegram_chat_id)

        # Confirm to owner
        owner_msg = (
            "Done! Action completed and requester notified."
            if approved
            else "Got it, request denied. Requester notified."
        )
        await self._notify_owner(owner_msg)
