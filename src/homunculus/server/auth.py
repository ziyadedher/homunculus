import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import aiosqlite
from aiohttp import web
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from homunculus.agent.tools.calendar import make_calendar_tools
from homunculus.agent.tools.email import make_email_tools
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.storage import store
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger

log = get_logger()

Service = Literal["calendar", "email"]

SESSION_TTL_MINUTES = 10
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
CALLBACK_PATH = "/auth/callback"

IDENTITY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
SERVICE_SCOPES: dict[Service, list[str]] = {
    "calendar": ["https://www.googleapis.com/auth/calendar"],
    "email": ["https://www.googleapis.com/auth/gmail.readonly"],
}


def _redirect_uri(config: ServeConfig, path: str) -> str:
    base = config.server.webhook_base_url
    if base is None:
        return f"http://localhost:{config.server.port}{path}"
    return f"{base.rstrip('/')}{path}"


def _make_flow(config: ServeConfig, scopes: list[str], redirect_uri: str, state: str) -> Flow:
    flow = Flow.from_client_secrets_file(
        str(config.google.credentials_path),
        scopes=scopes,
        state=state,
    )
    flow.redirect_uri = redirect_uri
    return flow


def _expires_at() -> str:
    return (datetime.now(UTC) + timedelta(minutes=SESSION_TTL_MINUTES)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


async def authenticate_request(request: web.Request) -> str | None:
    """AuthN only: extract Bearer token, validate via Google tokeninfo, return email."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer ") :]
    http_session = request.app["http_session"]
    async with http_session.get(GOOGLE_TOKENINFO_URL, params={"access_token": token}) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
    return data.get("email")


async def handle_auth_start(request: web.Request) -> web.Response:
    """POST /auth/start — begin identity OAuth flow."""
    config: ServeConfig = request.app["config"]
    db: aiosqlite.Connection = request.app["db"]

    session_id = uuid.uuid4().hex
    state = secrets.token_urlsafe(32)

    redirect_uri = _redirect_uri(config, CALLBACK_PATH)
    flow = _make_flow(config, IDENTITY_SCOPES, redirect_uri, state)
    auth_url, _ = flow.authorization_url(prompt="consent")

    await store.create_auth_session(
        db, session_id, "identity", state, _expires_at(), code_verifier=flow.code_verifier
    )

    log.info("auth_start", session_id=session_id)
    return web.json_response({"session_id": session_id, "auth_url": auth_url})


async def handle_auth_callback(request: web.Request) -> web.Response:
    """GET /auth/callback — unified Google OAuth callback for identity and service flows."""
    config: ServeConfig = request.app["config"]
    db: aiosqlite.Connection = request.app["db"]

    code = request.query.get("code")
    state = request.query.get("state")
    if not code or not state:
        return web.Response(status=400, text="Missing code or state")

    session = await store.get_auth_session_by_state(db, state)
    if session is None:
        return web.Response(status=400, text="Invalid or expired session")

    flow_type = str(session["flow_type"])

    if flow_type == "identity":
        return await _complete_identity(config, db, request, session, code, state)

    if flow_type in SERVICE_SCOPES:
        return await _complete_service(config, db, request.app, session, flow_type, code, state)

    return web.Response(status=400, text="Unknown flow type")


async def _complete_identity(
    config: ServeConfig,
    db: aiosqlite.Connection,
    request: web.Request,
    session: dict[str, object],
    code: str,
    state: str,
) -> web.Response:
    redirect_uri = _redirect_uri(config, CALLBACK_PATH)
    flow = _make_flow(config, IDENTITY_SCOPES, redirect_uri, state)
    flow.code_verifier = session.get("code_verifier")
    flow.fetch_token(code=code)

    creds = flow.credentials
    email = None
    if hasattr(creds, "id_token") and isinstance(creds.id_token, dict):
        email = creds.id_token.get("email")

    if email is None:
        http_session = request.app["http_session"]
        async with http_session.get(
            GOOGLE_TOKENINFO_URL,
            params={"access_token": creds.token},
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                email = data.get("email")

    if email is None:
        return web.Response(status=400, text="Could not determine email from OAuth flow")

    creds_json = creds.to_json()
    session_id = str(session["session_id"])
    await store.complete_identity_session(db, session_id, email, creds_json)

    log.info("auth_callback_success", email=email, session_id=session_id)
    return web.Response(
        content_type="text/html",
        text="<html><body><h2>Authenticated</h2>"
        "<p>You can close this tab and return to the CLI.</p></body></html>",
    )


async def _complete_service(
    config: ServeConfig,
    db: aiosqlite.Connection,
    app: web.Application,
    session: dict[str, object],
    service: str,
    code: str,
    state: str,
) -> web.Response:
    redirect_uri = _redirect_uri(config, CALLBACK_PATH)
    flow = _make_flow(config, SERVICE_SCOPES[service], redirect_uri, state)
    flow.code_verifier = session.get("code_verifier")
    flow.fetch_token(code=code)

    creds = flow.credentials
    creds_json = creds.to_json()

    session_id = str(session["session_id"])
    await store.complete_service_session(db, session_id, creds_json)
    await store.save_google_credentials(
        db, config.owner.email, service, creds_json, ",".join(SERVICE_SCOPES[service])
    )

    _reload_service_tools(app, service, creds)

    log.info("service_callback_success", service=service, session_id=session_id)
    return web.Response(
        content_type="text/html",
        text=f"<html><body><h2>{service.title()} access granted</h2>"
        "<p>You can close this tab and return to the CLI.</p></body></html>",
    )


async def handle_auth_status(request: web.Request) -> web.Response:
    """GET /auth/status/{session_id} — poll for auth completion."""
    db: aiosqlite.Connection = request.app["db"]
    session_id = request.match_info["session_id"]

    session = await store.get_auth_session(db, session_id)
    if session is None:
        return web.json_response({"error": "not found"}, status=404)

    if session["credentials_json"] is not None:
        return web.json_response(
            {
                "status": "complete",
                "credentials_json": session["credentials_json"],
                "email": session["email"],
            }
        )

    return web.json_response({"status": "pending"})


async def handle_auth_whoami(request: web.Request) -> web.Response:
    """GET /auth/whoami — return authenticated user's email and granted services."""
    config: ServeConfig = request.app["config"]
    db: aiosqlite.Connection = request.app["db"]

    email = await authenticate_request(request)
    if email is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    is_owner = email == config.owner.email
    services: list[str] = []
    if is_owner:
        for service in SERVICE_SCOPES:
            row = await store.get_google_credentials(db, email, service)
            if row is not None:
                services.append(service)

    return web.json_response({"email": email, "is_owner": is_owner, "services": services})


# --- Service auth ---


async def handle_service_start(request: web.Request) -> web.Response:
    """POST /auth/service/{service}/start — begin service delegation OAuth flow."""
    config: ServeConfig = request.app["config"]
    db: aiosqlite.Connection = request.app["db"]
    service = request.match_info["service"]

    if service not in SERVICE_SCOPES:
        return web.json_response({"error": f"unknown service: {service}"}, status=400)

    email = await authenticate_request(request)
    if email is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    if email != config.owner.email:
        return web.json_response({"error": "forbidden"}, status=403)

    session_id = uuid.uuid4().hex
    state = secrets.token_urlsafe(32)

    redirect_uri = _redirect_uri(config, CALLBACK_PATH)
    flow = _make_flow(config, SERVICE_SCOPES[service], redirect_uri, state)
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    await store.create_auth_session(
        db, session_id, service, state, _expires_at(), code_verifier=flow.code_verifier
    )

    log.info("service_auth_start", service=service, session_id=session_id, email=email)
    return web.json_response({"session_id": session_id, "auth_url": auth_url})


async def handle_service_status(request: web.Request) -> web.Response:
    """GET /auth/service/{service}/status/{session_id} — poll for service auth completion."""
    db: aiosqlite.Connection = request.app["db"]
    session_id = request.match_info["session_id"]

    session = await store.get_auth_session(db, session_id)
    if session is None:
        return web.json_response({"error": "not found"}, status=404)

    if session["credentials_json"] is not None:
        return web.json_response({"status": "complete"})

    return web.json_response({"status": "pending"})


def _reload_service_tools(app: web.Application, service: str, creds: Credentials) -> None:
    """Hot-reload tools for a specific service after credential grant."""
    config: ServeConfig = app["config"]
    registry: ToolRegistry = app["registry"]

    if service == "calendar" and config.google.calendar is not None:
        for tool in make_calendar_tools(creds, config.google.calendar.calendar_id):
            registry.register(tool)
        log.info("calendar_tools_reloaded")
    elif service == "email" and config.google.email is not None:
        for tool in make_email_tools(creds):
            registry.register(tool)
        log.info("email_tools_reloaded")


async def load_service_creds_from_db(
    db: aiosqlite.Connection, config: ServeConfig, service: str
) -> Credentials | None:
    """Load service credentials from DB."""
    scopes = SERVICE_SCOPES.get(service)
    if scopes is None:
        return None

    row = await store.get_google_credentials(db, config.owner.email, service)
    if row is not None:
        creds_json = str(row["credentials_json"])
        return Credentials.from_authorized_user_info(json.loads(creds_json), scopes)

    return None
