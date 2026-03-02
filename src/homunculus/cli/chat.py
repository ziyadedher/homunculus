import asyncio
import contextlib
import html
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import aiohttp
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.table import Table
from rich.text import Text

from homunculus.types import ConversationId, ConversationStatus
from homunculus.utils.logging import get_logger

log = get_logger()

POLL_INTERVAL_SECONDS = 2
OWNER_REFRESH_SECONDS = 5

_TOOLBAR_STYLE = Style.from_dict({"bottom-toolbar": "noreverse"})


# --- Shared helpers ---


def _format_relative_time(expires_at: str | None) -> str:
    if expires_at is None:
        return "\u2014"
    try:
        expires_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return "\u2014"
    delta = expires_dt - datetime.now(UTC)
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "expired"
    minutes = total_seconds // 60
    hours = minutes // 60
    if hours > 0:
        remaining_min = minutes % 60
        if remaining_min > 0:
            return f"in {hours}h {remaining_min}m"
        return f"in {hours}h"
    return f"in {minutes}m"


def _format_local_time(utc_str: str, tz_name: str) -> str:
    utc_dt = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    local_dt = utc_dt.astimezone(ZoneInfo(tz_name))
    return local_dt.strftime("%H:%M")


def _format_activity(message_count: int, total_requests: int) -> str:
    msg = f"{message_count} msg{'s' if message_count != 1 else ''}"
    if total_requests > 0:
        req = f"{total_requests} req{'s' if total_requests != 1 else ''}"
        return f"{msg} \u00b7 {req}"
    return msg


# --- User CLI (prompt_toolkit native output) ---


def _pt_agent(text: str) -> None:
    print_formatted_text(HTML(f"<ansiblue>Agent:</ansiblue> {html.escape(text)}"))


def _pt_dim(text: str) -> None:
    print_formatted_text(HTML(f"<ansidarkgray>{html.escape(text)}</ansidarkgray>"))


async def _api_post(
    http_session: aiohttp.ClientSession,
    server_url: str,
    path: str,
    token: str,
    json_body: dict[str, str],
) -> tuple[int, dict[str, object]]:
    """POST to the server API with Bearer auth."""
    headers = {"Authorization": f"Bearer {token}"}
    async with http_session.post(f"{server_url}{path}", json=json_body, headers=headers) as resp:
        data = await resp.json()
        return resp.status, data


async def _api_get(
    http_session: aiohttp.ClientSession,
    server_url: str,
    path: str,
    token: str,
) -> tuple[int, dict[str, object]]:
    """GET from the server API with Bearer auth."""
    headers = {"Authorization": f"Bearer {token}"}
    async with http_session.get(f"{server_url}{path}", headers=headers) as resp:
        data = await resp.json()
        return resp.status, data


async def _input_loop(
    session: PromptSession[str],
    toolbar: Callable[[], HTML],
    pending_ids: set[str],
    http_session: aiohttp.ClientSession,
    server_url: str,
    token: str,
    conversation_id: ConversationId,
) -> None:
    while True:
        try:
            line = await session.prompt_async("> ", bottom_toolbar=toolbar)
        except EOFError, KeyboardInterrupt:
            return
        if not line.strip():
            continue

        status, data = await _api_post(
            http_session,
            server_url,
            "/api/message",
            token,
            {"conversation_id": conversation_id, "body": line},
        )

        if status == 401:
            _pt_dim("Authentication failed. Run 'homunculus auth login' to re-authenticate.")
            continue
        if status != 200:
            _pt_dim(f"Server error ({status}): {data.get('error', 'unknown')}")
            continue

        response_text = data.get("response_text")
        if response_text:
            _pt_agent(str(response_text))

        approval_id = data.get("approval_id")
        escalation_message = data.get("escalation_message")
        if escalation_message and approval_id:
            pending_ids.add(str(approval_id))
            _pt_dim(f"Escalated to owner: {escalation_message}")


async def _poll_approvals(
    pending_ids: set[str],
    http_session: aiohttp.ClientSession,
    server_url: str,
    token: str,
) -> None:
    """Background task: poll tracked approval IDs via API, notify user on resolution."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

        resolved: list[str] = []
        for approval_id in list(pending_ids):
            status_code, data = await _api_get(
                http_session, server_url, f"/api/approvals/{approval_id}", token
            )
            if status_code == 404:
                resolved.append(approval_id)
                continue
            if status_code != 200:
                continue

            approval_status = data.get("status")
            if approval_status != "completed":
                continue  # not fully processed yet — wait for 'completed'

            resolved.append(approval_id)
            _pt_dim("Owner resolved your request.")
            response_text = data.get("response_text")
            if response_text:
                _pt_agent(str(response_text))

        for rid in resolved:
            pending_ids.discard(rid)


async def run_chat(server_url: str, token: str, conversation_id_str: str) -> None:
    """Chat via the server API.

    Authenticates with a saved API token, sends messages to the server's /api/message
    endpoint, and polls /api/approvals/{id} for escalation results.
    """
    pending_ids: set[str] = set()
    session: PromptSession[str] = PromptSession(style=_TOOLBAR_STYLE)
    conversation_id = ConversationId(conversation_id_str)

    _pt_dim(f"Connecting to {server_url}")
    log.info(
        "cli_chat_started",
        conversation_id=conversation_id,
        server_url=server_url,
    )

    def toolbar() -> HTML:
        n = len(pending_ids)
        parts: list[str] = []
        parts.append(f"<ansidarkgray>{html.escape(conversation_id_str)}</ansidarkgray>")
        if n > 0:
            parts.append(
                f"<style bg='ansiyellow' fg='ansiblack'>"
                f" {n} pending {'request' if n == 1 else 'requests'} "
                f"</style>"
            )
        return HTML(" ".join(parts))

    http_session = aiohttp.ClientSession()
    try:
        with patch_stdout():
            input_task = asyncio.create_task(
                _input_loop(
                    session,
                    toolbar,
                    pending_ids,
                    http_session,
                    server_url,
                    token,
                    conversation_id,
                )
            )
            poll_task = asyncio.create_task(
                _poll_approvals(
                    pending_ids,
                    http_session,
                    server_url,
                    token,
                )
            )
            _done, pending = await asyncio.wait(
                [input_task, poll_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    finally:
        await http_session.close()


# --- Owner CLI (cursor-home rendering) ---


def _build_owner_table(
    conversations: list[dict[str, object]],
    tz_name: str,
) -> Table:
    table = Table(
        box=None,
        show_header=True,
        header_style="bold dim",
        pad_edge=False,
        padding=(0, 2),
    )
    table.add_column("Conversation")
    table.add_column("Status")
    table.add_column("Updated")
    table.add_column("Expires")
    table.add_column("Activity")

    for conv in conversations:
        status = str(conv["status"])
        status_style = "cyan" if status == ConversationStatus.ACTIVE else "yellow"
        updated = _format_local_time(str(conv["updated_at"]), tz_name)
        expires = _format_relative_time(str(conv["expires_at"]) if conv["expires_at"] else None)
        msg_count = conv["message_count"]
        req_count = conv["total_requests"]
        activity = _format_activity(
            msg_count if isinstance(msg_count, int) else 0,
            req_count if isinstance(req_count, int) else 0,
        )
        table.add_row(
            str(conv["conversation_id"]),
            Text(status, style=status_style),
            updated,
            expires,
            activity,
        )

    return table
