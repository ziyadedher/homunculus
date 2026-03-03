import asyncio
import contextlib
import html
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.table import Table
from rich.text import Text

from homunculus.client import HomunculusClient
from homunculus.types import ConversationStatus
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


async def _input_loop(
    session: PromptSession[str],
    toolbar: Callable[[], HTML],
    pending_ids: set[str],
    client: HomunculusClient,
    contact_id: str | None,
) -> None:
    while True:
        try:
            line = await session.prompt_async("> ", bottom_toolbar=toolbar)
        except EOFError, KeyboardInterrupt:
            return
        if not line.strip():
            continue

        try:
            result = await client.send_message(line, override_client_id=contact_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                _pt_dim("Authentication failed. Run 'homunculus auth login' to re-authenticate.")
            else:
                _pt_dim(f"Server error ({e.response.status_code})")
            continue

        if result.response_text:
            _pt_agent(result.response_text)

        if result.request_message and result.request_id:
            pending_ids.add(result.request_id)
            _pt_dim(f"Sent to owner: {result.request_message}")


async def _poll_requests(
    pending_ids: set[str],
    client: HomunculusClient,
) -> None:
    """Background task: poll tracked request IDs via API, notify user on resolution."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

        resolved: list[str] = []
        for request_id in list(pending_ids):
            try:
                req = await client.get_request(request_id)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    resolved.append(request_id)
                continue

            if req.status != "completed":
                continue  # not fully processed yet — wait for 'completed'

            resolved.append(request_id)
            _pt_dim("Owner resolved your request.")
            if req.response_text:
                _pt_agent(req.response_text)

        for rid in resolved:
            pending_ids.discard(rid)


async def run_chat(client: HomunculusClient, contact_id: str | None = None) -> None:
    """Chat via the server API.

    Authenticates with a saved API token, sends messages to the server's /api/message
    endpoint, and polls /api/requests/{id} for escalation results.

    If contact_id is None, uses the authenticated user's own identity.
    """
    pending_ids: set[str] = set()
    session: PromptSession[str] = PromptSession(style=_TOOLBAR_STYLE)

    label = f"as {contact_id}" if contact_id else "as self"
    _pt_dim(f"Connecting to {client._server_url} ({label})")
    log.info(
        "cli_chat_started",
        contact_id=contact_id,
        server_url=client._server_url,
    )

    def toolbar() -> HTML:
        n = len(pending_ids)
        parts: list[str] = []
        toolbar_label = contact_id if contact_id else "self"
        parts.append(f"<ansidarkgray>{html.escape(toolbar_label)}</ansidarkgray>")
        if n > 0:
            parts.append(
                f"<style bg='ansiyellow' fg='ansiblack'>"
                f" {n} pending {'request' if n == 1 else 'requests'} "
                f"</style>"
            )
        return HTML(" ".join(parts))

    with patch_stdout():
        input_task = asyncio.create_task(
            _input_loop(
                session,
                toolbar,
                pending_ids,
                client,
                contact_id,
            )
        )
        poll_task = asyncio.create_task(
            _poll_requests(
                pending_ids,
                client,
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
