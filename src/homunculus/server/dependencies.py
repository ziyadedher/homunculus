from dataclasses import dataclass

import aiosqlite
import httpx
from fastapi import Depends, Header, HTTPException, Request

from homunculus.agent.tools.registry import ToolRegistry
from homunculus.channels.router import MessageRouter
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger

log = get_logger()

GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


@dataclass(frozen=True)
class AppState:
    config: ServeConfig
    db: aiosqlite.Connection
    registry: ToolRegistry
    router: MessageRouter
    http_client: httpx.AsyncClient
    webhook_secret: str


def get_state(request: Request) -> AppState:
    return request.app.state.app_state


def get_db(state: AppState = Depends(get_state)) -> aiosqlite.Connection:
    return state.db


def get_config(state: AppState = Depends(get_state)) -> ServeConfig:
    return state.config


def get_registry(state: AppState = Depends(get_state)) -> ToolRegistry:
    return state.registry


def get_router(state: AppState = Depends(get_state)) -> MessageRouter:
    return state.router


async def get_current_user(
    authorization: str = Header(default=""),
    state: AppState = Depends(get_state),
) -> str:
    """AuthN: validate Bearer token via Google tokeninfo, return email."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = authorization[len("Bearer ") :]
    resp = await state.http_client.get(GOOGLE_TOKENINFO_URL, params={"access_token": token})
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="unauthorized")
    data = resp.json()
    email = data.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="unauthorized")
    return email


async def require_owner(
    email: str = Depends(get_current_user),
    config: ServeConfig = Depends(get_config),
) -> str:
    """AuthZ: ensure the authenticated user is the owner."""
    if email != config.owner.email:
        raise HTTPException(status_code=403, detail="forbidden")
    return email
