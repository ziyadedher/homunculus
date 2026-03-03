import secrets

import aiosqlite
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from homunculus.channels.models import InboundMessage
from homunculus.channels.router import MessageRouter
from homunculus.channels.telegram import TelegramChannel
from homunculus.server.dependencies import (
    AppState,
    get_config,
    get_current_user,
    get_db,
    get_router,
    get_state,
    require_owner,
)
from homunculus.storage import store
from homunculus.types import ChannelId, ContactId, MessageId, RequestId, RequestStatus
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger
from homunculus.utils.tracing import get_tracer

log = get_logger()
tracer = get_tracer(__name__)

api_router = APIRouter(prefix="/api", tags=["api"])
webhook_router = APIRouter(tags=["webhook"])


class MessageRequest(BaseModel):
    body: str
    override_client_id: str | None = None


class MessageResponse(BaseModel):
    response_text: str | None
    request_message: str | None = None
    request_id: str | None = None


class RequestResponse(BaseModel):
    status: str
    response_text: str | None


class ResetResponse(BaseModel):
    status: str


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
    text = message.get("text", "")
    message_id = str(message.get("message_id", ""))

    if not chat_id or not text:
        return {"ok": True}

    with (
        structlog.contextvars.bound_contextvars(
            conversation_id=f"telegram:{chat_id}", message_id=message_id
        ),
        tracer.start_as_current_span("handle_telegram_webhook") as span,
    ):
        span.set_attribute("telegram.chat_id", chat_id)
        span.set_attribute("telegram.message_id", message_id)

        log.info("inbound_telegram", sender=chat_id, body_preview=text[:50])

        is_owner = chat_id == state.config.owner.telegram_chat_id
        contact = await store.get_contact_by_telegram_chat_id(state.db, chat_id)

        if contact is None:
            if is_owner:
                log.error("owner_contact_missing", chat_id=chat_id)
            else:
                log.info("unauthorized_sender", chat_id=chat_id)
                channel = state.router.get_channel(ChannelId.TELEGRAM)
                if isinstance(channel, TelegramChannel):
                    await channel.send_raw(
                        chat_id, "Sorry, you are not authorized to use this service."
                    )
            return {"ok": True}

        inbound = InboundMessage(
            contact=contact,
            is_owner=is_owner,
            body=text,
            channel_id=ChannelId.TELEGRAM,
            message_id=MessageId(message_id),
        )

        result = await state.router.handle_inbound(inbound)

        # Send response to the original sender
        if result.response_text and (channel := state.router.get_channel(ChannelId.TELEGRAM)):
            await channel.deliver(contact, result.response_text)

    return {"ok": True}


async def _handle_callback_query(state: AppState, callback_query: dict[str, object]) -> None:
    """Handle Telegram inline keyboard callback query for request buttons."""
    data = str(callback_query.get("data", ""))
    callback_query_id = str(callback_query.get("id", ""))
    from_user = callback_query.get("from")
    message_obj = callback_query.get("message")

    if not data or not callback_query_id:
        return

    # Parse callback data: approve:{id}, deny:{id}, option:{id}:{value}
    parts = data.split(":", 2)
    if len(parts) < 2:
        return

    action = parts[0]
    request_id_str = parts[1]

    if action == "option":
        if len(parts) != 3:
            return
        option_value = parts[2]
    elif action in ("approve", "deny"):
        option_value = None
    else:
        return

    # Verify sender is owner
    sender_chat_id = ""
    if isinstance(from_user, dict):
        sender_chat_id = str(from_user.get("id", ""))
    if sender_chat_id != state.config.owner.telegram_chat_id:
        return

    # Resolve request
    rid = RequestId(request_id_str)
    req = await store.get_request(state.db, rid)
    if req is None or req.status.value != "pending":
        # Try to answer callback query if we have a Telegram channel
        channel = state.router.get_channel(ChannelId.TELEGRAM)
        if isinstance(channel, TelegramChannel):
            await channel.answer_callback_query(
                callback_query_id, "Request not found or already resolved"
            )
        return

    if action == "approve":
        status = RequestStatus.APPROVED
        decision = "approved"
    elif action == "deny":
        status = RequestStatus.DENIED
        decision = "denied"
    else:
        # option
        status = RequestStatus.RESOLVED
        decision = "resolved"

    await store.resolve_request(state.db, rid, status)
    if option_value is not None:
        await store.save_request_response(state.db, rid, option_value)

    await store.log_action(
        state.db,
        action_type=f"request_{decision}",
        conversation_id=req.conversation_id,
        details={"request_id": request_id_str},
    )

    # Answer callback query (dismisses loading spinner)
    channel = state.router.get_channel(ChannelId.TELEGRAM)
    if isinstance(channel, TelegramChannel):
        if action == "approve":
            answer_text = "Approved!"
        elif action == "deny":
            answer_text = "Denied."
        else:
            answer_text = f"Selected: {option_value}"
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
                if action == "option":
                    status_label = f"SELECTED: {option_value}"
                else:
                    status_label = "APPROVED" if action == "approve" else "DENIED"
                new_text = f"{original_text}\n\n--- {status_label} ---"
                await channel.edit_message_text(chat_id, str(msg_id), new_text)

    # Refresh the request from DB (now has updated status)
    updated_req = await store.get_request(state.db, rid)
    if updated_req is not None:
        await state.router.handle_request_callback(updated_req, decision)


@api_router.post("/message", response_model=MessageResponse)
async def handle_api_message(
    body: MessageRequest,
    email: str = Depends(get_current_user),
    config: ServeConfig = Depends(get_config),
    db: aiosqlite.Connection = Depends(get_db),
    msg_router: MessageRouter = Depends(get_router),
) -> MessageResponse:
    is_owner = email == config.owner.email

    if body.override_client_id is not None:
        if not is_owner:
            raise HTTPException(status_code=403, detail="only the owner can override client_id")
        contact = await store.get_contact(db, ContactId(body.override_client_id))
        if contact is None:
            raise HTTPException(status_code=404, detail="contact not found")
    else:
        contact = await store.get_contact_by_email(db, email)
        if contact is None:
            raise HTTPException(status_code=403, detail="no contact for this email")

    with structlog.contextvars.bound_contextvars(conversation_id=f"api:{contact.contact_id}"):
        log.info("inbound_api", sender=contact.contact_id)

        message = InboundMessage(
            contact=contact,
            is_owner=is_owner,
            body=body.body,
            channel_id=ChannelId.API,
            message_id=MessageId(secrets.token_hex(16)),
        )

        result = await msg_router.handle_inbound(message)

        return MessageResponse(
            response_text=result.response_text,
            request_message=result.request_message or None,
            request_id=result.request_id or None,
        )


@api_router.post("/reset", response_model=ResetResponse)
async def handle_reset(
    hard: bool = False,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> ResetResponse:
    if hard:
        log.info("api_hard_reset", owner=owner_email)
        await store.hard_reset(state.db)
    else:
        log.info("api_soft_reset", owner=owner_email)
        await store.soft_reset(state.db)
    return ResetResponse(status="ok")


@api_router.get("/requests/{request_id}", response_model=RequestResponse)
async def handle_api_get_request(
    request_id: str,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> RequestResponse:
    req = await store.get_request(state.db, RequestId(request_id))
    if req is None:
        raise HTTPException(status_code=404, detail="not found")

    return RequestResponse(
        status=str(req.status),
        response_text=req.response_text,
    )
