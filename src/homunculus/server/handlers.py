import secrets

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from homunculus.channels.models import OutboundMessage, RawInboundMessage, Sender
from homunculus.channels.router import MessageRouter
from homunculus.channels.telegram import TelegramChannel
from homunculus.server.dependencies import AppState, get_router, get_state, require_owner
from homunculus.storage import store
from homunculus.types import ApprovalId, ApprovalStatus, ChannelId, ConversationId, MessageId
from homunculus.utils.logging import get_logger
from homunculus.utils.tracing import get_tracer

log = get_logger()
tracer = get_tracer(__name__)

api_router = APIRouter(prefix="/api", tags=["api"])
webhook_router = APIRouter(tags=["webhook"])


class MessageRequest(BaseModel):
    conversation_id: str
    body: str


class MessageResponse(BaseModel):
    response_text: str | None
    escalation_message: str | None = None
    approval_id: str | None = None


class ApprovalResponse(BaseModel):
    status: str
    response_text: str | None


@webhook_router.post("/webhook/telegram")
async def handle_telegram_webhook(request: Request) -> dict[str, bool]:
    state: AppState = request.app.state.app_state

    # Verify secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != state.webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    update = await request.json()

    # Handle callback queries (inline button presses)
    callback_query = update.get("callback_query")
    if callback_query is not None:
        await _handle_callback_query(state, callback_query)
        return {"ok": True}

    # Extract message from update
    message = update.get("message")
    if message is None:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    from_user = message.get("from", {})
    first_name = from_user.get("first_name", "")
    text = message.get("text", "")
    message_id = str(message.get("message_id", ""))

    if not chat_id or not text:
        return {"ok": True}

    structlog.contextvars.bind_contextvars(
        conversation_id=f"telegram:{chat_id}",
        message_id=message_id,
    )

    try:
        with tracer.start_as_current_span("handle_telegram_webhook") as span:
            span.set_attribute("telegram.chat_id", chat_id)
            span.set_attribute("telegram.message_id", message_id)

            log.info("inbound_telegram", sender=chat_id, body_preview=text[:50])

            inbound = RawInboundMessage(
                sender=Sender(identifier=chat_id, display_name=first_name or None),
                body=text,
                channel_id=ChannelId("telegram"),
                message_id=MessageId(message_id),
            )

            result = await state.router.handle_inbound(inbound)

            # Send response to the original sender
            if result is not None and result.response_text:
                channel = state.router.get_channel(ChannelId("telegram"))
                if channel is not None:
                    await channel.send(
                        OutboundMessage(
                            recipient_id=chat_id,
                            body=result.response_text,
                            channel_id=ChannelId("telegram"),
                        )
                    )
    finally:
        structlog.contextvars.unbind_contextvars("conversation_id", "message_id")

    return {"ok": True}


async def _handle_callback_query(state: AppState, callback_query: dict[str, object]) -> None:
    """Handle Telegram inline keyboard callback query for approval buttons."""
    data = str(callback_query.get("data", ""))
    callback_query_id = str(callback_query.get("id", ""))
    from_user = callback_query.get("from")
    message_obj = callback_query.get("message")

    if not data or not callback_query_id:
        return

    # Parse action and approval_id from callback data
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] not in ("approve", "deny"):
        return

    action, approval_id_str = parts
    approved = action == "approve"

    # Verify sender is owner
    sender_chat_id = ""
    if isinstance(from_user, dict):
        sender_chat_id = str(from_user.get("id", ""))
    if sender_chat_id != state.config.owner.telegram_chat_id:
        return

    # Resolve approval
    approval_id = ApprovalId(approval_id_str)
    approval = await store.get_approval(state.db, approval_id)
    if approval is None or approval.status.value != "pending":
        # Try to answer callback query if we have a Telegram channel
        channel = state.router.get_channel(ChannelId("telegram"))
        if isinstance(channel, TelegramChannel):
            await channel.answer_callback_query(
                callback_query_id, "Approval not found or already resolved"
            )
        return

    status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
    await store.resolve_approval(state.db, approval_id, status)
    await store.log_action(
        state.db,
        action_type=f"approval_{status}",
        conversation_id=approval.conversation_id,
        details={"approval_id": approval_id_str},
    )

    # Answer callback query (dismisses loading spinner)
    channel = state.router.get_channel(ChannelId("telegram"))
    if isinstance(channel, TelegramChannel):
        answer_text = "Approved!" if approved else "Denied."
        await channel.answer_callback_query(callback_query_id, answer_text)

        # Edit original message to show result
        if isinstance(message_obj, dict):
            chat = message_obj.get("chat")
            msg_id = message_obj.get("message_id")
            if isinstance(chat, dict) and msg_id is not None:
                chat_id = str(chat.get("id", ""))
                original_text = ""
                if isinstance(message_obj.get("text"), str):
                    original_text = str(message_obj["text"])
                status_label = "APPROVED" if approved else "DENIED"
                new_text = f"{original_text}\n\n--- {status_label} ---"
                await channel.edit_message_text(chat_id, str(msg_id), new_text)

    # Resume conversation via router
    await state.router.handle_approval_callback(approval, approved)


@api_router.post("/message", response_model=MessageResponse)
async def handle_api_message(
    body: MessageRequest,
    owner_email: str = Depends(require_owner),
    msg_router: MessageRouter = Depends(get_router),
) -> MessageResponse:
    structlog.contextvars.bind_contextvars(conversation_id=body.conversation_id)

    try:
        log.info("api_message", conversation_id=body.conversation_id, sender=owner_email)

        raw = RawInboundMessage(
            sender=Sender(identifier=owner_email),
            body=body.body,
            channel_id=ChannelId("api"),
            message_id=MessageId(secrets.token_hex(16)),
            conversation_id_override=ConversationId(body.conversation_id),
        )

        result = await msg_router.handle_inbound(raw)

        if result is None:
            return MessageResponse(response_text=None)

        return MessageResponse(
            response_text=result.response_text,
            escalation_message=result.escalation_message or None,
            approval_id=result.escalation_approval_id or None,
        )
    finally:
        structlog.contextvars.unbind_contextvars("conversation_id")


@api_router.get("/approvals/{approval_id}", response_model=ApprovalResponse)
async def handle_api_get_approval(
    approval_id: str,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> ApprovalResponse:
    approval = await store.get_approval(state.db, ApprovalId(approval_id))
    if approval is None:
        raise HTTPException(status_code=404, detail="not found")

    return ApprovalResponse(
        status=str(approval.status),
        response_text=approval.response_text,
    )
