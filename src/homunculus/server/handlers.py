import secrets

import aiosqlite
import structlog
from aiohttp import web

from homunculus.channels.models import OutboundMessage, RawInboundMessage, Sender
from homunculus.channels.router import MessageRouter
from homunculus.channels.telegram import TelegramChannel
from homunculus.server.auth import authenticate_request
from homunculus.storage import store
from homunculus.types import ApprovalId, ChannelId, ConversationId, MessageId
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger
from homunculus.utils.tracing import get_tracer

log = get_logger()
tracer = get_tracer(__name__)


async def handle_telegram_webhook(request: web.Request) -> web.Response:
    # Verify secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != request.app["webhook_secret"]:
        return web.Response(status=403, text="Forbidden")

    update = await request.json()

    # Extract message from update
    message = update.get("message")
    if message is None:
        return web.json_response({"ok": True})

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    from_user = message.get("from", {})
    first_name = from_user.get("first_name", "")
    text = message.get("text", "")
    message_id = str(message.get("message_id", ""))

    if not chat_id or not text:
        return web.json_response({"ok": True})

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

            msg_router: MessageRouter = request.app["router"]
            result = await msg_router.handle_inbound(inbound)

            # Send response to the original sender
            if result is not None and result.response_text:
                channel: TelegramChannel = request.app["channel"]
                await channel.send(
                    OutboundMessage(
                        recipient_id=chat_id,
                        body=result.response_text,
                        channel_id=ChannelId("telegram"),
                    )
                )
    finally:
        structlog.contextvars.unbind_contextvars("conversation_id", "message_id")

    return web.json_response({"ok": True})


async def handle_api_message(request: web.Request) -> web.Response:
    email = await authenticate_request(request)
    if email is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    body = await request.json()
    conversation_id_str = body.get("conversation_id")
    message_body = body.get("body")
    if not conversation_id_str or not message_body:
        return web.json_response({"error": "missing conversation_id or body"}, status=400)

    structlog.contextvars.bind_contextvars(conversation_id=conversation_id_str)

    try:
        log.info("api_message", conversation_id=conversation_id_str, sender=email)

        raw = RawInboundMessage(
            sender=Sender(identifier=email),
            body=message_body,
            channel_id=ChannelId("api"),
            message_id=MessageId(secrets.token_hex(16)),
            conversation_id_override=ConversationId(conversation_id_str),
        )

        msg_router: MessageRouter = request.app["router"]
        result = await msg_router.handle_inbound(raw)

        if result is None:
            return web.json_response({"response_text": None})

        response: dict[str, str | None] = {"response_text": result.response_text}
        if result.escalation_message:
            response["escalation_message"] = result.escalation_message
        if result.escalation_approval_id:
            response["approval_id"] = result.escalation_approval_id
        return web.json_response(response)
    finally:
        structlog.contextvars.unbind_contextvars("conversation_id")


async def handle_api_get_approval(request: web.Request) -> web.Response:
    email = await authenticate_request(request)
    if email is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    # AuthZ: only the owner can poll approvals
    config: ServeConfig = request.app["config"]
    if email != config.owner.email:
        return web.json_response({"error": "forbidden"}, status=403)

    approval_id = ApprovalId(request.match_info["id"])
    db: aiosqlite.Connection = request.app["db"]

    approval = await store.get_approval(db, approval_id)
    if approval is None:
        return web.json_response({"error": "not found"}, status=404)

    return web.json_response(
        {
            "status": str(approval.status),
            "response_text": approval.response_text,
        }
    )
