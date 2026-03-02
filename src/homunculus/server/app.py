import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI

from homunculus.agent.tools.contacts import make_contact_tools
from homunculus.agent.tools.location import make_location_tools
from homunculus.agent.tools.owner import make_owner_tools
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.base import Channel
from homunculus.channels.router import MessageRouter
from homunculus.channels.telegram import TELEGRAM_API_BASE, TelegramChannel
from homunculus.server.auth import (
    SERVICE_CONFIG_ATTR,
    SERVICE_SCOPES,
    load_service_creds_from_db,
    reload_service_tools,
)
from homunculus.server.auth import (
    router as auth_router,
)
from homunculus.server.dependencies import AppState
from homunculus.server.handlers import api_router, webhook_router
from homunculus.storage import store
from homunculus.storage.store import open_store
from homunculus.types import ChannelId
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger

log = get_logger()

REAPER_INTERVAL_SECONDS = 60


async def _reaper_loop(state: AppState) -> None:
    """Periodically clean up expired conversations and auth sessions."""
    while True:
        await asyncio.sleep(REAPER_INTERVAL_SECONDS)
        count = await store.cleanup_expired(state.db)
        if count > 0:
            log.info("reaper_cleanup", expired_count=count)
        sessions_count = await store.cleanup_expired_sessions(state.db)
        if sessions_count > 0:
            log.info("reaper_sessions_cleanup", expired_count=sessions_count)


async def _register_telegram_webhook(
    http_client: httpx.AsyncClient, bot_token: str, webhook_url: str, secret_token: str
) -> None:
    """Register the Telegram webhook via setWebhook API."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/setWebhook"
    payload = {
        "url": webhook_url,
        "secret_token": secret_token,
    }
    resp = await http_client.post(url, json=payload)
    body = resp.json()
    if resp.status_code == 200 and body.get("ok"):
        log.info("telegram_webhook_registered", url=webhook_url)
    else:
        log.error("telegram_webhook_registration_failed", status=resp.status_code, body=body)


def create_app(config: ServeConfig) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # HTTP client
        http_client = httpx.AsyncClient()

        # Storage
        db = await open_store(config.storage.db_path)

        # Tool registry
        registry = ToolRegistry()
        for tool in make_owner_tools(db):
            registry.register(tool)
        for tool in make_contact_tools(db):
            registry.register(tool)
        if config.google.maps is not None:
            for tool in make_location_tools(config.google.maps.api_key):
                registry.register(tool)

        # Service tools: load credentials from DB and register tools
        for service in SERVICE_SCOPES:
            svc_config = getattr(config.google, SERVICE_CONFIG_ATTR[service], None)
            if svc_config is not None:
                creds = await load_service_creds_from_db(db, config, service)
                if creds is not None:
                    reload_service_tools(registry, config, service, creds)
                else:
                    log.warning(
                        "service_creds_not_found",
                        service=service,
                        hint=f"run 'homunculus auth grant {service}' to grant",
                    )

        # Channel
        channel = TelegramChannel(config.telegram, http_client)
        channels: dict[ChannelId, Channel] = {ChannelId("telegram"): channel}

        # Router
        router = MessageRouter(config=config, db=db, registry=registry, channels=channels)

        # Webhook secret for Telegram verification
        webhook_secret = secrets.token_hex(32)

        # Build app state
        app_state = AppState(
            config=config,
            db=db,
            registry=registry,
            router=router,
            http_client=http_client,
            webhook_secret=webhook_secret,
        )
        app.state.app_state = app_state

        # Register Telegram webhook if base URL is configured
        if config.server.webhook_base_url is not None:
            webhook_url = f"{config.server.webhook_base_url.rstrip('/')}/webhook/telegram"
            await _register_telegram_webhook(
                http_client, config.telegram.bot_token, webhook_url, webhook_secret
            )

        # Reaper background task
        reaper_task = asyncio.create_task(_reaper_loop(app_state))

        log.info("app_created", host=config.server.host, port=config.server.port)

        yield

        # Shutdown
        reaper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper_task
        await db.close()
        await http_client.aclose()

    app = FastAPI(lifespan=lifespan)

    # Include routers
    app.include_router(api_router)
    app.include_router(webhook_router)
    app.include_router(auth_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
