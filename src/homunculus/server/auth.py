import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import aiosqlite
from aiohttp import web
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from homunculus.agent.tools.calendar import make_calendar_tools
from homunculus.agent.tools.gmail import make_gmail_tools
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.storage import store
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger

log = get_logger()

SESSION_TTL_MINUTES = 10
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

IDENTITY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
SERVICE_SCOPES: dict[str, list[str]] = {
    "calendar": ["https://www.googleapis.com/auth/calendar"],
    "gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
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

    redirect_uri = _redirect_uri(config, "/auth/callback")
    flow = _make_flow(config, IDENTITY_SCOPES, redirect_uri, state)
    auth_url, _ = flow.authorization_url(prompt="consent")

    await store.create_auth_session(
        db, session_id, "identity", state, _expires_at(), code_verifier=flow.code_verifier
    )

    log.info("auth_start", session_id=session_id)
    return web.json_response({"session_id": session_id, "auth_url": auth_url})


async def handle_auth_callback(request: web.Request) -> web.Response:
    """GET /auth/callback — Google redirects here after identity auth."""
    config: ServeConfig = request.app["config"]
    db: aiosqlite.Connection = request.app["db"]

    code = request.query.get("code")
    state = request.query.get("state")
    if not code or not state:
        return web.Response(status=400, text="Missing code or state")

    session = await store.get_auth_session_by_state(db, state)
    if session is None or session["flow_type"] != "identity":
        return web.Response(status=400, text="Invalid or expired session")

    redirect_uri = _redirect_uri(config, "/auth/callback")
    flow = _make_flow(config, IDENTITY_SCOPES, redirect_uri, state)
    flow.code_verifier = session.get("code_verifier")
    flow.fetch_token(code=code)

    creds = flow.credentials
    # Extract email from ID token
    email = None
    if hasattr(creds, "id_token") and isinstance(creds.id_token, dict):
        email = creds.id_token.get("email")

    if email is None:
        # Fallback: use tokeninfo endpoint
        http_session = request.app["http_session"]
        async with http_session.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"access_token": creds.token},
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                email = data.get("email")

    if email is None:
        return web.Response(status=400, text="Could not determine email from OAuth flow")

    # Store Google credentials in session for CLI to pick up
    creds_json = creds.to_json()
    session_id = str(session["session_id"])
    await store.complete_identity_session(db, session_id, email, creds_json)

    log.info("auth_callback_success", email=email, session_id=session_id)
    return web.Response(
        content_type="text/html",
        text="<html><body><h2>Authenticated</h2>"
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


# --- Generic service auth (calendar, gmail, etc.) ---


async def handle_service_start(request: web.Request) -> web.Response:
    """POST /auth/service/{service}/start — begin service delegation OAuth flow."""
    config: ServeConfig = request.app["config"]
    db: aiosqlite.Connection = request.app["db"]
    service = request.match_info["service"]

    if service not in SERVICE_SCOPES:
        return web.json_response({"error": f"unknown service: {service}"}, status=400)

    # AuthN
    email = await authenticate_request(request)
    if email is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    # AuthZ: only the owner can delegate service access
    if email != config.owner.email:
        return web.json_response({"error": "forbidden"}, status=403)

    session_id = uuid.uuid4().hex
    state = secrets.token_urlsafe(32)

    redirect_uri = _redirect_uri(config, f"/auth/service/{service}/callback")
    flow = _make_flow(config, SERVICE_SCOPES[service], redirect_uri, state)
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    await store.create_auth_session(
        db, session_id, service, state, _expires_at(), code_verifier=flow.code_verifier
    )

    log.info("service_auth_start", service=service, session_id=session_id, email=email)
    return web.json_response({"session_id": session_id, "auth_url": auth_url})


async def handle_service_callback(request: web.Request) -> web.Response:
    """GET /auth/service/{service}/callback — Google redirects here after service auth."""
    config: ServeConfig = request.app["config"]
    db: aiosqlite.Connection = request.app["db"]
    service = request.match_info["service"]

    if service not in SERVICE_SCOPES:
        return web.json_response({"error": f"unknown service: {service}"}, status=400)

    code = request.query.get("code")
    state = request.query.get("state")
    if not code or not state:
        return web.Response(status=400, text="Missing code or state")

    session = await store.get_auth_session_by_state(db, state)
    if session is None or session["flow_type"] != service:
        return web.Response(status=400, text="Invalid or expired session")

    redirect_uri = _redirect_uri(config, f"/auth/service/{service}/callback")
    flow = _make_flow(config, SERVICE_SCOPES[service], redirect_uri, state)
    flow.code_verifier = session.get("code_verifier")
    flow.fetch_token(code=code)

    creds = flow.credentials
    creds_json = creds.to_json()

    # Store in DB
    session_id = str(session["session_id"])
    await store.complete_service_session(db, session_id, creds_json)
    await store.save_google_credentials(
        db, config.owner.email, service, creds_json, ",".join(SERVICE_SCOPES[service])
    )

    # Hot-reload service tools
    _reload_service_tools(request.app, service, creds)

    log.info("service_callback_success", service=service, session_id=session_id)
    return web.Response(
        content_type="text/html",
        text=f"<html><body><h2>{service.title()} access granted</h2>"
        "<p>You can close this tab and return to the CLI.</p></body></html>",
    )


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
    elif service == "gmail" and config.google.gmail is not None:
        for tool in make_gmail_tools(creds):
            registry.register(tool)
        log.info("gmail_tools_reloaded")


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
