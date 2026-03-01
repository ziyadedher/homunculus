import json
from dataclasses import replace

import aiosqlite

from homunculus.agent.loop import process_message
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.base import Channel
from homunculus.channels.models import InboundMessage, OutboundMessage
from homunculus.storage import store
from homunculus.types import ApprovalId, ApprovalStatus, ContactId, ConversationId
from homunculus.utils.config import Config
from homunculus.utils.logging import get_logger
from homunculus.utils.tracing import get_tracer

log = get_logger()
tracer = get_tracer(__name__)


class MessageRouter:
    def __init__(
        self,
        config: Config,
        db: aiosqlite.Connection,
        registry: ToolRegistry,
        channel: Channel,
    ) -> None:
        self._config = config
        self._db = db
        self._registry = registry
        self._channel = channel

    async def handle_inbound(self, message: InboundMessage) -> None:
        await store.log_action(
            self._db,
            action_type="inbound_message",
            conversation_id=message.conversation_id,
            details={"sender": message.sender.phone, "body": message.body},
        )

        # Check if this is the owner responding to a pending approval
        if message.sender.phone == self._config.owner.phone:
            handled = await self._handle_owner_reply(message)
            if handled:
                return

        # Look up contact by phone
        contact = await store.get_contact_by_phone(self._db, message.sender.phone)
        if contact is None:
            log.info("unauthorized_sender", phone=message.sender.phone)
            await self._channel.send(
                OutboundMessage(
                    recipient_phone=message.sender.phone,
                    body="Sorry, you are not authorized to use this service.",
                    channel_id=self._channel.channel_id,
                )
            )
            return

        # Attach contact_id to message for conversation routing
        message = replace(message, contact_id=ContactId(str(contact["contact_id"])))

        # Regular message — send to agent
        result = await process_message(
            message_body=message.body,
            conversation_id=message.conversation_id,
            config=self._config,
            db=self._db,
            registry=self._registry,
            contact=contact,
        )

        # Send response to the original sender
        if result.response_text:
            await self._channel.send(
                OutboundMessage(
                    recipient_phone=message.sender.phone,
                    body=result.response_text,
                    channel_id=self._channel.channel_id,
                )
            )

        # If escalation happened, notify the owner
        if result.escalation_message:
            await self._channel.send(
                OutboundMessage(
                    recipient_phone=self._config.owner.phone,
                    body=result.escalation_message,
                    channel_id=self._channel.channel_id,
                )
            )

    async def _handle_owner_reply(self, message: InboundMessage) -> bool:
        approval = await store.get_oldest_pending_approval(self._db)
        if approval is None:
            return False  # No pending approvals — treat as regular message

        cid = ConversationId(str(approval["conversation_id"]))

        body_lower = message.body.strip().lower()
        approved = body_lower in ("yes", "y", "approve", "ok", "sure", "yep", "yeah")
        denied = body_lower in ("no", "n", "deny", "nope", "nah", "cancel")

        if not approved and not denied:
            return False  # Ambiguous response — treat as regular message

        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
        await store.resolve_approval(self._db, ApprovalId(str(approval["id"])), status)
        await store.log_action(
            self._db,
            action_type=f"approval_{status}",
            conversation_id=cid,
            details={"approval_id": approval["id"]},
        )

        with tracer.start_as_current_span("escalation.owner_reply") as span:
            span.set_attribute("approval.id", str(approval["id"]))
            span.set_attribute("approval.status", str(status))
            await self._resume_after_approval(approval, cid, approved)

        return True

    async def _resume_after_approval(
        self, approval: dict[str, object], cid: ConversationId, approved: bool
    ) -> None:
        tool_name = str(approval["tool_name"])
        tool_input = approval["tool_input"]
        if isinstance(tool_input, str):
            tool_input = json.loads(tool_input)

        if approved:
            resume_body = (
                f"Owner approved request {approval['id']}. "
                f"The approved action is: {tool_name}({json.dumps(tool_input)}). "
                f"Please execute it now."
            )
        else:
            resume_body = (
                f"Owner denied request {approval['id']}. "
                f"Inform the requester that the request was denied."
            )

        # Look up contact from conversation_id (format: channel:contact_id)
        contact_id_str = cid.split(":", 1)[1]
        contact = await store.get_contact(self._db, ContactId(contact_id_str))

        agent_result = await process_message(
            message_body=resume_body,
            conversation_id=cid,
            config=self._config,
            db=self._db,
            registry=self._registry,
            contact=contact,
            approved_tools={tool_name} if approved and tool_name else None,
        )

        # Send agent response to the original requester
        if contact is not None and contact["phone"] is not None:
            requester_phone = str(contact["phone"])
        else:
            requester_phone = contact_id_str

        if agent_result.response_text:
            await self._channel.send(
                OutboundMessage(
                    recipient_phone=requester_phone,
                    body=agent_result.response_text,
                    channel_id=self._channel.channel_id,
                )
            )

        # Confirm to owner
        owner_msg = (
            "Done! Action completed and requester notified."
            if approved
            else "Got it, request denied. Requester notified."
        )
        await self._channel.send(
            OutboundMessage(
                recipient_phone=self._config.owner.phone,
                body=owner_msg,
                channel_id=self._channel.channel_id,
            )
        )
