import asyncio
import contextlib
import secrets

import aiohttp
import aiosqlite
from aiohttp import web

from homunculus.agent.tools.contacts import make_contact_tools
from homunculus.agent.tools.location import make_location_tools
from homunculus.agent.tools.owner import make_owner_tools
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.base import Channel
from homunculus.channels.router import MessageRouter
from homunculus.channels.telegram import TELEGRAM_API_BASE, TelegramChannel
from homunculus.server.auth import (
    _reload_service_tools,
    handle_auth_callback,
    handle_auth_start,
    handle_auth_status,
    handle_auth_whoami,
    handle_service_start,
    handle_service_status,
    load_service_creds_from_db,
)
from homunculus.server.handlers import (
    handle_api_get_approval,
    handle_api_message,
    handle_telegram_webhook,
)
from homunculus.storage import store
from homunculus.storage.store import open_store
from homunculus.types import ChannelId
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger

log = get_logger()

REAPER_INTERVAL_SECONDS = 60


async def _reaper_loop(db: aiosqlite.Connection) -> None:
    """Periodically clean up expired conversations and auth sessions."""
    while True:
        await asyncio.sleep(REAPER_INTERVAL_SECONDS)
        count = await store.cleanup_expired(db)
        if count > 0:
            log.info("reaper_cleanup", expired_count=count)
        sessions_count = await store.cleanup_expired_sessions(db)
        if sessions_count > 0:
            log.info("reaper_sessions_cleanup", expired_count=sessions_count)


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


async def create_app(config: ServeConfig) -> web.Application:
    app = web.Application()

    # HTTP client session
    session = aiohttp.ClientSession()
    app["http_session"] = session

    # Storage
    db = await open_store(config.storage.db_path)
    app["db"] = db

    # Tool registry
    registry = ToolRegistry()
    for tool in make_owner_tools(db):
        registry.register(tool)
    for tool in make_contact_tools(db):
        registry.register(tool)
    if config.google.maps is not None:
        for tool in make_location_tools(config.google.maps.api_key):
            registry.register(tool)

    app["registry"] = registry

    # Store config for API handlers (needed before tool loading)
    app["config"] = config

    # Service tools: load credentials from DB and register tools
    for service, svc_attr in (("calendar", "calendar"), ("email", "email")):
        svc_config = getattr(config.google, svc_attr, None)
        if svc_config is not None:
            creds = await load_service_creds_from_db(db, config, service)
            if creds is not None:
                _reload_service_tools(app, service, creds)
            else:
                log.warning(
                    "service_creds_not_found",
                    service=service,
                    hint=f"run 'homunculus auth grant {service}' to grant",
                )

    # Channel
    channel = TelegramChannel(config.telegram, session)
    app["channel"] = channel

    # Router
    channels: dict[ChannelId, Channel] = {ChannelId("telegram"): channel}
    router = MessageRouter(config=config, db=db, registry=registry, channels=channels)
    app["router"] = router

    # Webhook secret for Telegram verification
    webhook_secret = secrets.token_hex(32)
    app["webhook_secret"] = webhook_secret

    # Reaper background task
    app["reaper"] = asyncio.create_task(_reaper_loop(db))

    # Routes
    app.router.add_get("/health", handle_health)
    app.router.add_post("/webhook/telegram", handle_telegram_webhook)
    app.router.add_post("/api/message", handle_api_message)
    app.router.add_get("/api/approvals/{id}", handle_api_get_approval)

    # Auth routes
    app.router.add_post("/auth/start", handle_auth_start)
    app.router.add_get("/auth/callback", handle_auth_callback)
    app.router.add_get("/auth/status/{session_id}", handle_auth_status)
    app.router.add_get("/auth/whoami", handle_auth_whoami)
    app.router.add_post("/auth/service/{service}/start", handle_service_start)
    app.router.add_get("/auth/service/{service}/status/{session_id}", handle_service_status)

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


async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


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
