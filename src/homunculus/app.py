import asyncio
import contextlib
from urllib.parse import parse_qs

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
from homunculus.channels.twilio_sms import TwilioSmsChannel
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


async def create_app(config: Config) -> web.Application:
    assert config.twilio is not None, "Twilio config required for server mode"
    assert config.google_calendar is not None, "Google Calendar config required for server mode"

    app = web.Application()

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
    channel = TwilioSmsChannel(config.twilio)
    app["channel"] = channel

    # Router
    router = MessageRouter(config=config, db=db, registry=registry, channel=channel)
    app["router"] = router

    # Reaper background task
    app["reaper"] = asyncio.create_task(_reaper_loop(db))

    # Routes
    app.router.add_get("/health", _handle_health)
    app.router.add_post("/webhook/sms", _handle_sms_webhook)

    # Cleanup
    app.on_cleanup.append(_cleanup)

    log.info("app_created", host=config.server.host, port=config.server.port)
    return app


async def _handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _handle_sms_webhook(request: web.Request) -> web.Response:
    body = await request.read()
    params = parse_qs(body.decode("utf-8"))

    from_number = params.get("From", [""])[0]
    message_body = params.get("Body", [""])[0]
    message_sid = params.get("MessageSid", [""])[0]

    if not from_number or not message_body:
        return web.Response(status=400, text="Missing From or Body")

    structlog.contextvars.bind_contextvars(
        conversation_id=f"sms:{from_number}",
        message_id=message_sid,
    )

    try:
        with tracer.start_as_current_span("handle_sms_webhook") as span:
            span.set_attribute("sms.sender", from_number)
            span.set_attribute("sms.message_id", message_sid)

            log.info("inbound_sms", sender=from_number, body_preview=message_body[:50])

            inbound = InboundMessage(
                sender=Sender(phone=from_number),
                body=message_body,
                channel_id=ChannelId("sms"),
                message_id=MessageId(message_sid),
            )

            router: MessageRouter = request.app["router"]
            await router.handle_inbound(inbound)
    finally:
        structlog.contextvars.unbind_contextvars("conversation_id", "message_id")

    # Return empty TwiML response (we send replies via REST API, not TwiML)
    return web.Response(
        text='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        content_type="application/xml",
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
