import asyncio
import contextlib
import html
import json
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import aiosqlite
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.table import Table
from rich.text import Text

from homunculus.agent.loop import process_message
from homunculus.agent.tools.calendar import make_calendar_tools
from homunculus.agent.tools.contacts import make_contact_tools
from homunculus.agent.tools.location import make_location_tools
from homunculus.agent.tools.owner import make_owner_tools
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.calendar.google import get_credentials
from homunculus.storage import store
from homunculus.storage.store import open_store
from homunculus.types import ApprovalStatus, ContactId, ConversationId
from homunculus.utils.config import Config
from homunculus.utils.logging import get_logger

log = get_logger()

POLL_INTERVAL_SECONDS = 2
OWNER_REFRESH_SECONDS = 5

_TOOLBAR_STYLE = Style.from_dict({"bottom-toolbar": "noreverse"})


def _build_registry(config: Config, db: aiosqlite.Connection) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in make_owner_tools(db):
        registry.register(tool)
    for tool in make_contact_tools(db):
        registry.register(tool)

    if config.google_calendar is not None:
        creds = get_credentials(
            credentials_path=config.google_calendar.credentials_path,
            token_path=config.google_calendar.token_path,
        )
        for tool in make_calendar_tools(creds, config.google_calendar.calendar_id):
            registry.register(tool)

    if config.google_maps is not None:
        for tool in make_location_tools(config.google_maps.api_key):
            registry.register(tool)

    return registry


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


_REJECTION_MESSAGE = "Sorry, you are not authorized to use this service."


async def _input_loop(
    session: PromptSession[str],
    toolbar: Callable[[], HTML],
    pending_ids: set[str],
    config: Config,
    db: aiosqlite.Connection,
    registry: ToolRegistry,
    conversation_id: ConversationId,
    contact: dict[str, object] | None,
) -> None:
    while True:
        try:
            line = await session.prompt_async("> ", bottom_toolbar=toolbar)
        except EOFError, KeyboardInterrupt:
            return
        if not line.strip():
            continue

        # No contact → simulate rejection like the real router does
        if contact is None:
            _pt_agent(_REJECTION_MESSAGE)
            continue

        result = await process_message(line, conversation_id, config, db, registry, contact=contact)

        if result.response_text:
            _pt_agent(result.response_text)

        if result.escalation_message and result.escalation_approval_id:
            pending_ids.add(result.escalation_approval_id)
            _pt_dim(f"Escalated to owner: {result.escalation_message}")


async def _poll_approvals(
    pending_ids: set[str],
    config: Config,
    db: aiosqlite.Connection,
    registry: ToolRegistry,
    conversation_id: ConversationId,
    contact: dict[str, object] | None,
) -> None:
    """Background task: poll tracked approval IDs, notify user on resolution."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

        await store.cleanup_expired(db)

        resolved: list[str] = []
        for approval_id in list(pending_ids):
            approval = await store.get_approval(db, approval_id)
            if approval is None:
                resolved.append(approval_id)
                continue
            if approval["status"] == ApprovalStatus.PENDING:
                continue

            resolved.append(approval_id)
            approved = approval["status"] == ApprovalStatus.APPROVED

            tool_name = str(approval["tool_name"])
            tool_input = approval["tool_input"]
            if isinstance(tool_input, str):
                tool_input = json.loads(tool_input)

            if approved:
                follow_up = (
                    f"Owner approved request {approval['id']}. "
                    f"The approved action is: {tool_name}({json.dumps(tool_input)}). "
                    f"Please execute it now."
                )
            else:
                follow_up = (
                    f"Owner denied request {approval['id']}. "
                    f"Inform the requester that the request was denied."
                )

            _pt_dim(f"Processing approval resolution for {approval['id']}...")
            result = await process_message(
                follow_up,
                conversation_id,
                config,
                db,
                registry,
                contact=contact,
                approved_tools={tool_name},
            )

            if approved:
                print_formatted_text(HTML("<ansigreen>Owner approved your request.</ansigreen>"))
            else:
                print_formatted_text(HTML("<ansired>Owner denied your request.</ansired>"))
            if result.response_text:
                _pt_agent(result.response_text)

        for rid in resolved:
            pending_ids.discard(rid)


async def run_chat(config: Config, conversation_id_str: str) -> None:
    """Chat as any conversation ID — simulates the full inbound flow.

    Best-effort contact lookup from the conversation ID (parses "channel:identifier").
    If a contact is found, messages are processed by the agent normally.
    If no contact is found, messages are rejected locally (no API call),
    matching the behaviour of the real SMS router.
    """
    db = await open_store(config.storage.db_path)
    registry = _build_registry(config, db)
    pending_ids: set[str] = set()
    session: PromptSession[str] = PromptSession(style=_TOOLBAR_STYLE)
    conversation_id = ConversationId(conversation_id_str)

    # Best-effort contact lookup from the identifier part
    contact: dict[str, object] | None = None
    if ":" in conversation_id_str:
        identifier = conversation_id_str.split(":", 1)[1]
        # Try as contact_id first, then phone, then email
        contact = await store.get_contact(db, ContactId(identifier))
        if contact is None:
            contact = await store.get_contact_by_phone(db, identifier)
        if contact is None:
            contact = await store.get_contact_by_email(db, identifier)

    if contact is not None:
        _pt_dim(f"Resolved contact: {contact['name']} ({contact['contact_id']})")
    else:
        _pt_dim(
            f"No contact found for '{conversation_id_str}'. "
            f"Messages will be rejected (no API calls)."
        )

    log.info("cli_chat_started", conversation_id=conversation_id)

    def toolbar() -> HTML:
        n = len(pending_ids)
        parts: list[str] = []
        if contact is None:
            parts.append("<ansired>unauthorized</ansired>")
        parts.append(f"<ansidarkgray>{html.escape(conversation_id_str)}</ansidarkgray>")
        if n > 0:
            parts.append(
                f"<style bg='ansiyellow' fg='ansiblack'>"
                f" {n} pending {'request' if n == 1 else 'requests'} "
                f"</style>"
            )
        return HTML(" ".join(parts))

    try:
        with patch_stdout():
            input_task = asyncio.create_task(
                _input_loop(
                    session,
                    toolbar,
                    pending_ids,
                    config,
                    db,
                    registry,
                    conversation_id,
                    contact,
                )
            )
            poll_task = asyncio.create_task(
                _poll_approvals(
                    pending_ids,
                    config,
                    db,
                    registry,
                    conversation_id,
                    contact,
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
        await db.close()


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
        status_style = "cyan" if status == "active" else "yellow"
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
