import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, get_args

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel

from homunculus.agent.tools.calendar import make_calendar_tools
from homunculus.agent.tools.email import make_email_tools
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.server.dependencies import (
    GOOGLE_TOKENINFO_URL,
    AppState,
    get_current_user,
    get_db,
    get_state,
    require_owner,
)
from homunculus.storage import store
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger

log = get_logger()


class AuthStartResponse(BaseModel):
    session_id: str
    auth_url: str


class AuthStatusResponse(BaseModel):
    status: str
    credentials_json: str | None = None
    email: str | None = None


class WhoamiResponse(BaseModel):
    email: str
    is_owner: bool
    services: list[str]


class ServiceStatusResponse(BaseModel):
    status: str


Service = Literal["calendar", "email"]
_VALID_SERVICES: set[Service] = set(get_args(Service))

SESSION_TTL_MINUTES = 10
CALLBACK_PATH = "/auth/callback"

IDENTITY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
SERVICE_SCOPES: dict[Service, list[str]] = {
    "calendar": ["https://www.googleapis.com/auth/calendar"],
    "email": ["https://www.googleapis.com/auth/gmail.readonly"],
}

# Maps service name to the corresponding GoogleConfig attribute name.
SERVICE_CONFIG_ATTR: dict[Service, str] = {"calendar": "calendar", "email": "email"}

router = APIRouter(prefix="/auth", tags=["auth"])


def _validate_service(value: str) -> Service | None:
    if value in _VALID_SERVICES:
        return value  # type: ignore[return-value]
    return None


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


def _get_credentials(flow: Flow) -> Credentials:
    creds = flow.credentials
    assert isinstance(creds, Credentials)
    return creds


def _expires_at() -> str:
    return (datetime.now(UTC) + timedelta(minutes=SESSION_TTL_MINUTES)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def reload_service_tools(
    registry: ToolRegistry, config: ServeConfig, service: Service, creds: Credentials
) -> None:
    """Hot-reload tools for a specific service after credential grant."""
    if service == "calendar" and config.google.calendar is not None:
        for tool in make_calendar_tools(creds, config.google.calendar.calendar_id):
            registry.register(tool)
        log.info("calendar_tools_reloaded")
    elif service == "email" and config.google.email is not None:
        for tool in make_email_tools(creds):
            registry.register(tool)
        log.info("email_tools_reloaded")


async def load_service_creds_from_db(
    db: aiosqlite.Connection, config: ServeConfig, service: Service
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


# --- Identity auth ---


@router.post("/start", response_model=AuthStartResponse)
async def handle_auth_start(state: AppState = Depends(get_state)) -> AuthStartResponse:
    """Begin identity OAuth flow."""
    config = state.config
    db = state.db

    session_id = uuid.uuid4().hex
    oauth_state = secrets.token_urlsafe(32)

    redirect_uri = _redirect_uri(config, CALLBACK_PATH)
    flow = _make_flow(config, IDENTITY_SCOPES, redirect_uri, oauth_state)
    auth_url, _ = flow.authorization_url(prompt="consent")

    await store.create_auth_session(
        db, session_id, "identity", oauth_state, _expires_at(), code_verifier=flow.code_verifier
    )

    log.info("auth_start", session_id=session_id)
    return AuthStartResponse(session_id=session_id, auth_url=auth_url)


@router.get("/callback")
async def handle_auth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
) -> HTMLResponse:
    """Unified Google OAuth callback for identity and service flows."""
    app_state: AppState = request.app.state.app_state
    config = app_state.config
    db = app_state.db

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    session = await store.get_auth_session_by_state(db, state)
    if session is None:
        raise HTTPException(status_code=400, detail="Invalid or expired session")

    flow_type = str(session["flow_type"])

    if flow_type == "identity":
        return await _complete_identity(config, db, app_state, session, code, state)

    service = _validate_service(flow_type)
    if service is not None:
        return await _complete_service(config, db, app_state, session, service, code, state)

    raise HTTPException(status_code=400, detail="Unknown flow type")


async def _complete_identity(
    config: ServeConfig,
    db: aiosqlite.Connection,
    app_state: AppState,
    session: dict[str, object],
    code: str,
    state: str,
) -> HTMLResponse:
    redirect_uri = _redirect_uri(config, CALLBACK_PATH)
    flow = _make_flow(config, IDENTITY_SCOPES, redirect_uri, state)
    flow.code_verifier = session.get("code_verifier")
    flow.fetch_token(code=code)

    creds = _get_credentials(flow)
    email = None
    id_token = getattr(creds, "id_token", None)
    if isinstance(id_token, dict):
        email = id_token.get("email")

    if email is None:
        resp = await app_state.http_client.get(
            GOOGLE_TOKENINFO_URL,
            params={"access_token": creds.token},
        )
        if resp.status_code == 200:
            data = resp.json()
            email = data.get("email")

    if email is None:
        raise HTTPException(status_code=400, detail="Could not determine email from OAuth flow")

    creds_json = creds.to_json()
    session_id = str(session["session_id"])
    await store.complete_identity_session(db, session_id, email, creds_json)

    log.info("auth_callback_success", email=email, session_id=session_id)
    return HTMLResponse(
        "<html><body><h2>Authenticated</h2>"
        "<p>You can close this tab and return to the CLI.</p></body></html>"
    )


async def _complete_service(
    config: ServeConfig,
    db: aiosqlite.Connection,
    app_state: AppState,
    session: dict[str, object],
    service: Service,
    code: str,
    state: str,
) -> HTMLResponse:
    redirect_uri = _redirect_uri(config, CALLBACK_PATH)
    flow = _make_flow(config, SERVICE_SCOPES[service], redirect_uri, state)
    flow.code_verifier = session.get("code_verifier")
    flow.fetch_token(code=code)

    creds = _get_credentials(flow)
    creds_json = creds.to_json()

    session_id = str(session["session_id"])
    await store.complete_service_session(db, session_id, creds_json)
    await store.save_google_credentials(
        db, config.owner.email, service, creds_json, ",".join(SERVICE_SCOPES[service])
    )

    reload_service_tools(app_state.registry, config, service, creds)

    log.info("service_callback_success", service=service, session_id=session_id)
    return HTMLResponse(
        f"<html><body><h2>{service.title()} access granted</h2>"
        "<p>You can close this tab and return to the CLI.</p></body></html>"
    )


@router.get("/status/{session_id}", response_model=AuthStatusResponse)
async def handle_auth_status(
    session_id: str,
    db: aiosqlite.Connection = Depends(get_db),
) -> AuthStatusResponse:
    """Poll for identity auth completion."""
    session = await store.get_auth_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="not found")

    if session["credentials_json"] is not None:
        return AuthStatusResponse(
            status="complete",
            credentials_json=str(session["credentials_json"]),
            email=str(session["email"]),
        )

    return AuthStatusResponse(status="pending")


@router.get("/whoami", response_model=WhoamiResponse)
async def handle_auth_whoami(
    email: str = Depends(get_current_user),
    state: AppState = Depends(get_state),
) -> WhoamiResponse:
    """Return authenticated user's email and granted services."""
    config = state.config
    db = state.db

    is_owner = email == config.owner.email
    services: list[str] = []
    if is_owner:
        for service in SERVICE_SCOPES:
            row = await store.get_google_credentials(db, email, service)
            if row is not None:
                services.append(service)

    return WhoamiResponse(email=email, is_owner=is_owner, services=services)


# --- Service auth ---


@router.post("/service/{service}/start", response_model=AuthStartResponse)
async def handle_service_start(
    service: str,
    owner_email: str = Depends(require_owner),
    app_state: AppState = Depends(get_state),
) -> AuthStartResponse:
    """Begin service delegation OAuth flow."""
    config = app_state.config
    db = app_state.db

    validated = _validate_service(service)
    if validated is None:
        raise HTTPException(status_code=400, detail=f"unknown service: {service}")

    session_id = uuid.uuid4().hex
    oauth_state = secrets.token_urlsafe(32)

    redirect_uri = _redirect_uri(config, CALLBACK_PATH)
    flow = _make_flow(config, SERVICE_SCOPES[validated], redirect_uri, oauth_state)
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    await store.create_auth_session(
        db, session_id, validated, oauth_state, _expires_at(), code_verifier=flow.code_verifier
    )

    log.info("service_auth_start", service=validated, session_id=session_id, email=owner_email)
    return AuthStartResponse(session_id=session_id, auth_url=auth_url)


@router.get("/service/{service}/status/{session_id}", response_model=ServiceStatusResponse)
async def handle_service_status(
    service: str,
    session_id: str,
    db: aiosqlite.Connection = Depends(get_db),
) -> ServiceStatusResponse:
    """Poll for service auth completion."""
    session = await store.get_auth_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="not found")

    if session["credentials_json"] is not None:
        return ServiceStatusResponse(status="complete")

    return ServiceStatusResponse(status="pending")
