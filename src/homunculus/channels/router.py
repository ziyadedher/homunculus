import json

import aiosqlite

from homunculus.agent.loop import AgentResult, process_message
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.base import Channel
from homunculus.channels.models import InboundMessage, OutboundMessage, RawInboundMessage
from homunculus.channels.telegram import TelegramChannel
from homunculus.storage import store
from homunculus.types import (
    Approval,
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

    def get_channel(self, channel_id: ChannelId) -> Channel | None:
        return self._channels.get(channel_id)

    async def handle_inbound(self, raw: RawInboundMessage) -> AgentResult | None:
        """Route an inbound message through auth, agent processing, and escalation.

        Returns AgentResult if the message was processed by the agent, or None if
        handled internally (approval reply) or rejected (unauthorized sender).
        Callers are responsible for delivering the response to the original sender.
        """
        # Authenticate first: resolve identity, reject unknowns
        message = await self._authenticate(raw)
        if message is None:
            return None

        await store.log_action(
            self._db,
            action_type="inbound_message",
            conversation_id=message.conversation_id,
            details={"sender": message.contact.contact_id, "body": message.body},
        )

        # Fetch pending approvals if the sender is the owner
        pending_approvals = None
        if message.is_owner:
            pending_approvals = await store.get_pending_approvals(self._db)

        # Regular message — send to agent
        result = await process_message(
            message_body=message.body,
            conversation_id=message.conversation_id,
            config=self._config,
            db=self._db,
            registry=self._registry,
            contact=message.contact,
            pending_approvals=pending_approvals,
        )

        # If escalation happened, notify the owner with inline buttons
        if result.escalation_message and result.escalation_approval_id:
            await self._notify_owner_with_buttons(
                result.escalation_message, result.escalation_approval_id
            )
        elif result.escalation_message:
            await self._notify_owner(result.escalation_message)

        return result

    async def handle_approval_callback(self, approval: Approval, approved: bool) -> None:
        """Handle an approval decision from an inline button callback.

        Called by the webhook handler after resolving the approval in the DB.
        """
        with tracer.start_as_current_span("escalation.callback_approval") as span:
            span.set_attribute("approval.id", str(approval.id))
            span.set_attribute("approval.approved", approved)
            await self._resume_after_approval(approval, approved)

    async def _authenticate(self, raw: RawInboundMessage) -> InboundMessage | None:
        """Resolve identity from a raw message, rejecting unknown senders.

        Returns an authenticated InboundMessage with a resolved Contact, or None
        if the sender cannot be identified (with a rejection message sent on
        Telegram).
        """
        is_owner = self._check_is_owner(raw)
        contact = await self._resolve_contact(raw)

        if contact is None and is_owner:
            # Synthesize a Contact from owner config
            owner = self._config.owner
            contact = Contact(
                contact_id=ContactId("owner"),
                name=owner.name,
                email=owner.email,
                telegram_chat_id=owner.telegram_chat_id,
                timezone=owner.timezone,
            )

        if contact is None:
            log.info("unauthorized_sender", identifier=raw.sender.identifier)
            if raw.channel_id == ChannelId("telegram"):
                await self._send_via_channel(
                    ChannelId("telegram"),
                    raw.sender.identifier,
                    "Sorry, you are not authorized to use this service.",
                )
            return None

        return InboundMessage(
            contact=contact,
            is_owner=is_owner,
            body=raw.body,
            channel_id=raw.channel_id,
            message_id=raw.message_id,
            timestamp=raw.timestamp,
            conversation_id_override=raw.conversation_id_override,
        )

    def _check_is_owner(self, raw: RawInboundMessage) -> bool:
        if raw.channel_id == ChannelId("telegram"):
            return raw.sender.identifier == self._config.owner.telegram_chat_id
        if raw.channel_id == ChannelId("api"):
            return raw.sender.identifier == self._config.owner.email
        return False

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

    async def _notify_owner_with_buttons(self, body: str, approval_id: str) -> None:
        """Send an escalation to the owner with Approve/Deny inline buttons."""
        channel = self._channels.get(ChannelId("telegram"))
        if isinstance(channel, TelegramChannel):
            buttons = [
                [
                    {"text": "Approve", "callback_data": f"approve:{approval_id}"},
                    {"text": "Deny", "callback_data": f"deny:{approval_id}"},
                ]
            ]
            try:
                await channel.send_with_inline_keyboard(
                    self._config.owner.telegram_chat_id, body, buttons
                )
                return
            except Exception:
                log.warning("notify_owner_buttons_failed")
        # Fallback to plain text
        await self._notify_owner(body)

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
