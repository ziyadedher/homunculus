import asyncio
import contextlib
import secrets

import aiohttp
import aiosqlite
import structlog
from aiohttp import web

from homunculus.agent.tools.calendar import make_calendar_tools
from homunculus.agent.tools.contacts import make_contact_tools
from homunculus.agent.tools.location import make_location_tools
from homunculus.agent.tools.owner import make_owner_tools
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.calendar.google import get_credentials
from homunculus.channels.models import InboundMessage, Sender
from homunculus.channels.router import MessageRouter
from homunculus.channels.telegram import TELEGRAM_API_BASE, TelegramChannel
from homunculus.storage import store
from homunculus.storage.store import open_store
from homunculus.types import ChannelId, MessageId
from homunculus.utils.config import Config
from homunculus.utils.logging import get_logger
from homunculus.utils.tracing import get_tracer

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

    # Routes
    app.router.add_get("/health", _handle_health)
    app.router.add_post("/webhook/telegram", _handle_telegram_webhook)

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
