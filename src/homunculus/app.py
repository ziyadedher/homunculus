import asyncio
import contextlib
import secrets

import aiohttp
import aiosqlite
import structlog
from aiohttp import web

from homunculus.agent.loop import process_message
from homunculus.agent.tools.calendar import make_calendar_tools
from homunculus.agent.tools.contacts import make_contact_tools
from homunculus.agent.tools.location import make_location_tools
from homunculus.agent.tools.owner import make_owner_tools
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.calendar.google import get_credentials
from homunculus.channels.models import InboundMessage, OutboundMessage, Sender
from homunculus.channels.router import MessageRouter
from homunculus.channels.telegram import TELEGRAM_API_BASE, TelegramChannel
from homunculus.storage import store
from homunculus.storage.store import open_store
from homunculus.types import ApprovalId, ChannelId, ContactId, ConversationId, MessageId
from homunculus.utils.config import Config
from homunculus.utils.logging import get_logger
from homunculus.utils.tracing import get_tracer

GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

log = get_logger()
tracer = get_tracer(__name__)

REAPER_INTERVAL_SECONDS = 60


async def _reaper_loop(db: aiosqlite.Connection) -> None:
    """Periodically clean up expired conversations."""
    while True:
        await asyncio.sleep(REAPER_INTERVAL_SECONDS)
        count = await store.cleanup_expired(db)
        if count > 0:
            log.info("reaper_cleanup", expired_count=count)


async def _register_telegram_webhook(
    session: aiohttp.ClientSession, bot_token: str, webhook_url: str, secret_token: str
) -> None:
    """Register the Telegram webhook via setWebhook API."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/setWebhook"
    payload = {
        "url": webhook_url,
        "secret_token": secret_token,
    }
    async with session.post(url, json=payload) as resp:
        body = await resp.json()
        if resp.status == 200 and body.get("ok"):
            log.info("telegram_webhook_registered", url=webhook_url)
        else:
            log.error("telegram_webhook_registration_failed", status=resp.status, body=body)


async def create_app(config: Config) -> web.Application:
    assert config.telegram is not None, "Telegram config required for server mode"
    assert config.google_calendar is not None, "Google Calendar config required for server mode"

    app = web.Application()

    # HTTP client session
    session = aiohttp.ClientSession()
    app["http_session"] = session

    # Storage
    db = await open_store(config.storage.db_path)
    app["db"] = db

    # Google Calendar credentials
    creds = get_credentials(
        credentials_path=config.google_calendar.credentials_path,
        token_path=config.google_calendar.token_path,
    )

    # Tool registry
    registry = ToolRegistry()
    for tool in make_calendar_tools(creds, config.google_calendar.calendar_id):
        registry.register(tool)
    for tool in make_owner_tools(db):
        registry.register(tool)
    for tool in make_contact_tools(db):
        registry.register(tool)
    if config.google_maps is not None:
        for tool in make_location_tools(config.google_maps.api_key):
            registry.register(tool)
    app["registry"] = registry

    # Channel
    channel = TelegramChannel(config.telegram, session)
    app["channel"] = channel

    # Router
    router = MessageRouter(config=config, db=db, registry=registry, channel=channel)
    app["router"] = router

    # Webhook secret for Telegram verification
    webhook_secret = secrets.token_hex(32)
    app["webhook_secret"] = webhook_secret

    # Reaper background task
    app["reaper"] = asyncio.create_task(_reaper_loop(db))

    # Store config for API handlers
    app["config"] = config

    # Routes
    app.router.add_get("/health", _handle_health)
    app.router.add_post("/webhook/telegram", _handle_telegram_webhook)
    app.router.add_post("/api/message", _handle_api_message)
    app.router.add_get("/api/approvals/{id}", _handle_api_get_approval)

    # Register Telegram webhook if base URL is configured
    if config.server.webhook_base_url is not None:
        webhook_url = f"{config.server.webhook_base_url.rstrip('/')}/webhook/telegram"
        await _register_telegram_webhook(
            session, config.telegram.bot_token, webhook_url, webhook_secret
        )

    # Cleanup
    app.on_cleanup.append(_cleanup)

    log.info("app_created", host=config.server.host, port=config.server.port)
    return app


async def _handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _handle_telegram_webhook(request: web.Request) -> web.Response:
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

            inbound = InboundMessage(
                sender=Sender(identifier=chat_id, display_name=first_name or None),
                body=text,
                channel_id=ChannelId("telegram"),
                message_id=MessageId(message_id),
            )

            msg_router: MessageRouter = request.app["router"]
            await msg_router.handle_inbound(inbound)
    finally:
        structlog.contextvars.unbind_contextvars("conversation_id", "message_id")

    return web.json_response({"ok": True})


async def _validate_google_token(session: aiohttp.ClientSession, token: str) -> str | None:
    """Validate a Google OAuth access token and return the email if valid."""
    async with session.get(GOOGLE_TOKENINFO_URL, params={"access_token": token}) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        return data.get("email")


async def _authenticate_request(request: web.Request) -> str | None:
    """Extract and validate Bearer token, return email if authorized."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer ") :]
    session: aiohttp.ClientSession = request.app["http_session"]
    email = await _validate_google_token(session, token)
    if email is None:
        return None
    config: Config = request.app["config"]
    if email != config.owner.email:
        log.warning("api_auth_email_mismatch", got=email, expected=config.owner.email)
        return None
    return email


async def _handle_api_message(request: web.Request) -> web.Response:
    email = await _authenticate_request(request)
    if email is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    body = await request.json()
    conversation_id_str = body.get("conversation_id")
    message_body = body.get("body")
    if not conversation_id_str or not message_body:
        return web.json_response({"error": "missing conversation_id or body"}, status=400)

    conversation_id = ConversationId(conversation_id_str)
    config: Config = request.app["config"]
    db: aiosqlite.Connection = request.app["db"]
    registry: ToolRegistry = request.app["registry"]
    channel: TelegramChannel = request.app["channel"]

    structlog.contextvars.bind_contextvars(conversation_id=conversation_id_str)

    try:
        # Look up contact from conversation_id (format: channel:identifier)
        contact = None
        if ":" in conversation_id_str:
            identifier = conversation_id_str.split(":", 1)[1]
            contact = await store.get_contact(db, ContactId(identifier))
            if contact is None:
                contact = await store.get_contact_by_telegram_chat_id(db, identifier)
            if contact is None:
                contact = await store.get_contact_by_phone(db, identifier)
            if contact is None:
                contact = await store.get_contact_by_email(db, identifier)

        await store.log_action(
            db,
            action_type="api_message",
            conversation_id=conversation_id,
            details={"sender_email": email, "body": message_body},
        )

        log.info("api_message", conversation_id=conversation_id_str, sender=email)

        result = await process_message(
            message_body=message_body,
            conversation_id=conversation_id,
            config=config,
            db=db,
            registry=registry,
            contact=contact,
        )

        # If escalation happened, notify the owner via Telegram
        if result.escalation_message:
            try:
                await channel.send(
                    OutboundMessage(
                        recipient_id=config.owner.telegram_chat_id,
                        body=result.escalation_message,
                        channel_id=channel.channel_id,
                    )
                )
            except Exception:
                log.warning("api_escalation_send_failed")

        response: dict[str, str | None] = {"response_text": result.response_text}
        if result.escalation_message:
            response["escalation_message"] = result.escalation_message
        if result.escalation_approval_id:
            response["approval_id"] = result.escalation_approval_id
        return web.json_response(response)
    finally:
        structlog.contextvars.unbind_contextvars("conversation_id")


async def _handle_api_get_approval(request: web.Request) -> web.Response:
    email = await _authenticate_request(request)
    if email is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    approval_id = ApprovalId(request.match_info["id"])
    db: aiosqlite.Connection = request.app["db"]

    approval = await store.get_approval(db, approval_id)
    if approval is None:
        return web.json_response({"error": "not found"}, status=404)

    return web.json_response(
        {
            "status": str(approval["status"]),
            "response_text": approval.get("response_text"),
        }
    )


async def _cleanup(app: web.Application) -> None:
    reaper = app.get("reaper")
    if reaper:
        reaper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper

    db = app.get("db")
    if db:
        await db.close()

    session = app.get("http_session")
    if session:
        await session.close()
