import json

import aiosqlite

from homunculus.agent.loop import AgentResult, process_message
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.base import Channel
from homunculus.channels.models import InboundMessage
from homunculus.channels.telegram import TelegramChannel
from homunculus.storage import store
from homunculus.types import (
    ChannelId,
    Contact,
    ContactId,
    ConversationId,
    Message,
    OwnerRequest,
    RequestType,
)
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger
from homunculus.utils.tracing import get_tracer

log = get_logger()
tracer = get_tracer(__name__)


def _conversation_id(channel_id: ChannelId, contact: Contact) -> ConversationId:
    """Derive a conversation ID from a channel and contact."""
    return ConversationId(f"{channel_id}:{contact.contact_id}")


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

    async def handle_inbound(self, message: InboundMessage) -> AgentResult:
        """Route an authenticated inbound message through agent processing and escalation.

        Takes an already-authenticated InboundMessage (handlers are responsible for
        identity resolution and rejection of unknown senders). Returns AgentResult.
        Callers are responsible for delivering the response to the original sender.
        """
        await store.log_action(
            self._db,
            action_type="inbound_message",
            conversation_id=message.conversation_id,
            details={"sender": message.contact.contact_id, "body": message.body},
        )

        # Fetch pending requests if the sender is the owner
        pending_requests = None
        if message.is_owner:
            pending_requests = await store.get_pending_requests(self._db)

        # Regular message — send to agent
        result = await process_message(
            message_body=message.body,
            conversation_id=message.conversation_id,
            config=self._config,
            db=self._db,
            registry=self._registry,
            contact=message.contact,
            pending_requests=pending_requests,
        )

        # If a request was created, notify the owner
        if result.request_message and result.request_id:
            req = await store.get_request(self._db, result.request_id)
            if req is not None:
                await self._notify_owner_for_request(req)
        elif result.request_message:
            await self._notify_owner(result.request_message)

        # If freeform questions were resolved this turn, resume those conversations
        for rid in result.resolved_request_ids:
            req = await store.get_request(self._db, rid)
            if req is not None:
                await self._resume_after_resolution(req)

        return result

    async def handle_request_callback(self, request: OwnerRequest, decision: str) -> None:
        """Handle a request decision from an inline button callback.

        Called by the webhook handler after resolving the request in the DB.
        """
        with tracer.start_as_current_span("request.callback") as span:
            span.set_attribute("request.id", str(request.id))
            span.set_attribute("request.decision", decision)
            await self._resume_after_resolution(request)

    async def _get_owner_contact(self) -> Contact | None:
        """Look up the owner's Contact record."""
        return await store.get_contact_by_telegram_chat_id(
            self._db, self._config.owner.telegram_chat_id
        )

    async def _notify_owner(self, body: str) -> None:
        """Send a notification to the owner via Telegram.

        Persists the message to the owner's Telegram conversation history so the
        agent has context when the owner replies.
        """
        owner_contact = await self._get_owner_contact()
        if owner_contact is None:
            log.warning("notify_owner_failed", reason="owner_contact_missing")
            return
        try:
            await self._send_via_channel(ChannelId.TELEGRAM, owner_contact, body)
        except Exception:
            log.warning("notify_owner_failed")

    async def _notify_owner_for_request(self, req: OwnerRequest) -> None:
        """Send request notification to the owner, dispatching by request type.

        All notification types are persisted to the owner's conversation history.
        """
        owner_contact = await self._get_owner_contact()
        if owner_contact is None:
            log.warning("notify_owner_for_request_failed", reason="owner_contact_missing")
            return

        channel = self._channels.get(ChannelId.TELEGRAM)
        if not isinstance(channel, TelegramChannel):
            await self._notify_owner(req.description)
            return

        owner_convo = _conversation_id(ChannelId.TELEGRAM, owner_contact)

        if req.request_type == RequestType.APPROVAL:
            buttons = [
                [
                    {"text": "Approve", "callback_data": f"approve:{req.id}"},
                    {"text": "Deny", "callback_data": f"deny:{req.id}"},
                ]
            ]
            try:
                await channel.send_with_inline_keyboard(
                    self._config.owner.telegram_chat_id, req.description, buttons
                )
                await store.append_message(
                    self._db, owner_convo, Message.assistant(req.description)
                )
                return
            except Exception:
                log.warning("notify_owner_buttons_failed")
        elif req.request_type == RequestType.OPTIONS and req.options:
            buttons = [
                [{"text": opt, "callback_data": f"option:{req.id}:{opt}"}] for opt in req.options
            ]
            try:
                await channel.send_with_inline_keyboard(
                    self._config.owner.telegram_chat_id, req.description, buttons
                )
                await store.append_message(
                    self._db, owner_convo, Message.assistant(req.description)
                )
                return
            except Exception:
                log.warning("notify_owner_options_failed")
        else:
            # FREEFORM: plain text, falls through to _notify_owner which persists
            pass

        await self._notify_owner(req.description)

    async def _send_via_channel(
        self,
        channel_id: ChannelId,
        contact: Contact,
        body: str,
    ) -> None:
        """Send a message to a contact via a channel and persist to conversation history."""
        convo_id = _conversation_id(channel_id, contact)

        channel = self._channels.get(channel_id)
        if channel is not None:
            await channel.deliver(contact, body)

        await store.append_message(self._db, convo_id, Message.assistant(body))

    async def _resume_after_resolution(self, req: OwnerRequest) -> None:
        # Build resume message based on request type and resolution
        if req.request_type == RequestType.APPROVAL:
            if req.status in ("approved", "completed"):
                resume_body = (
                    f"Owner approved request {req.id}. "
                    f"The approved action is: {req.tool_name}({json.dumps(req.tool_input)}). "
                    f"Please execute it now."
                )
            else:
                resume_body = (
                    f"Owner denied request {req.id}. "
                    f"Inform the requester that the request was denied."
                )
        elif req.request_type == RequestType.OPTIONS:
            resume_body = (
                f"Owner selected '{req.response_text}' for request {req.id}. Proceed accordingly."
            )
        else:
            # FREEFORM
            resume_body = (
                f"Owner answered request {req.id}: '{req.response_text}'. "
                f"Use this to continue the conversation."
            )

        # Look up contact from conversation_id (format: channel:contact_id)
        contact_id_str = req.conversation_id.split(":", 1)[1]
        contact = await store.get_contact(self._db, ContactId(contact_id_str))

        # approved_tools only for APPROVAL + approved with non-empty tool_name
        approved_tools = None
        if (
            req.request_type == RequestType.APPROVAL
            and req.status in ("approved", "completed")
            and req.tool_name
        ):
            approved_tools = {req.tool_name}

        agent_result = await process_message(
            message_body=resume_body,
            conversation_id=req.conversation_id,
            config=self._config,
            db=self._db,
            registry=self._registry,
            contact=contact,
            approved_tools=approved_tools,
        )

        # Store the response and mark request as completed
        if agent_result.response_text:
            await store.save_request_response(self._db, req.id, agent_result.response_text)
        await store.complete_request(self._db, req.id)

        # Best-effort send to the original requester via Telegram
        if contact is not None and agent_result.response_text:
            try:
                await self._send_via_channel(
                    ChannelId.TELEGRAM,
                    contact,
                    agent_result.response_text,
                )
            except Exception:
                log.warning("send_to_requester_failed", contact_id=contact.contact_id)

        # Confirm to owner
        if req.request_type == RequestType.APPROVAL:
            if req.status in ("approved", "completed"):
                owner_msg = "Done! Action completed and requester notified."
            else:
                owner_msg = "Got it, request denied. Requester notified."
        else:
            owner_msg = "Done! Response sent to requester."
        await self._notify_owner(owner_msg)
