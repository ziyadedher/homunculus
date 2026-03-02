"""Async API client for the Homunculus server."""

import asyncio
import json
from pathlib import Path
from types import TracebackType

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

from homunculus.server.auth import WhoamiResponse
from homunculus.server.handlers import (
    MessageResponse,
    RequestResponse,
    ResetResponse,
)
from homunculus.utils.logging import get_logger

log = get_logger()

DEFAULT_CREDENTIALS_PATH = Path("~/.config/homunculus/credentials.json")


class HomunculusClient:
    """Async client for the Homunculus API.

    Handles Google OAuth credential loading, token refresh, and all API endpoints.
    Supports use as an async context manager.
    """

    def __init__(
        self,
        server_url: str,
        credentials_path: Path | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._credentials_path = (credentials_path or DEFAULT_CREDENTIALS_PATH).expanduser()
        self._creds: Credentials | None = None
        self._http = httpx.AsyncClient()

    def _load_and_refresh_token(self) -> str:
        """Load Google credentials from file, refresh if expired, return access token."""
        creds = self._creds
        if creds is None:
            if not self._credentials_path.exists():
                msg = (
                    f"No credentials found at {self._credentials_path}\n"
                    "Run 'homunculus auth login' first."
                )
                raise FileNotFoundError(msg)
            creds_data = json.loads(self._credentials_path.read_text())
            creds = Credentials.from_authorized_user_info(creds_data)
            self._creds = creds

        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            self._credentials_path.write_text(creds.to_json())

        return creds.token

    def _headers(self) -> dict[str, str]:
        token = self._load_and_refresh_token()
        return {"Authorization": f"Bearer {token}"}

    async def health(self) -> dict[str, str]:
        resp = await self._http.get(f"{self._server_url}/health")
        resp.raise_for_status()
        return resp.json()

    async def whoami(self) -> WhoamiResponse:
        resp = await self._http.get(f"{self._server_url}/auth/whoami", headers=self._headers())
        resp.raise_for_status()
        return WhoamiResponse.model_validate(resp.json())

    async def send_message(
        self,
        body: str,
        override_client_id: str | None = None,
    ) -> MessageResponse:
        payload: dict[str, str] = {"body": body}
        if override_client_id is not None:
            payload["override_client_id"] = override_client_id
        resp = await self._http.post(
            f"{self._server_url}/api/message",
            json=payload,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return MessageResponse.model_validate(resp.json())

    async def get_request(self, request_id: str) -> RequestResponse:
        resp = await self._http.get(
            f"{self._server_url}/api/requests/{request_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return RequestResponse.model_validate(resp.json())

    async def reset(self) -> ResetResponse:
        resp = await self._http.post(
            f"{self._server_url}/api/reset",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return ResetResponse.model_validate(resp.json())

    async def send_and_poll(
        self,
        body: str,
        override_client_id: str | None = None,
        poll_interval: float = 2.0,
        timeout: float = 120.0,
    ) -> MessageResponse:
        """Send a message; if a request_id is returned, poll until resolved or timeout."""
        result = await self.send_message(body, override_client_id=override_client_id)
        if result.request_id is None:
            return result

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(poll_interval)
            req = await self.get_request(result.request_id)
            if req.status == "completed":
                return MessageResponse(
                    response_text=req.response_text,
                    request_message=result.request_message,
                    request_id=result.request_id,
                )

        return result

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> HomunculusClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
