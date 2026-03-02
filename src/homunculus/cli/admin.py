import asyncio
import contextlib
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum

import aiosqlite
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.application import Application
from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from rich.console import Console
from rich.table import Table

from homunculus.cli.chat import (
    OWNER_REFRESH_SECONDS,
    _build_owner_table,
    _format_activity,
    _format_local_time,
    _format_relative_time,
)
from homunculus.storage import store
from homunculus.storage.store import open_store
from homunculus.types import (
    Approval,
    ApprovalStatus,
    Contact,
    ContactId,
    ConversationId,
    ConversationStatus,
    Message,
)
from homunculus.utils.config import AdminConfig
from homunculus.utils.logging import get_logger
from homunculus.utils.validation import (
    VALID_TIMEZONES,
    validate_email,
    validate_phone,
    validate_telegram_chat_id,
    validate_timezone,
)

log = get_logger()

_TZ_COMPLETER = FuzzyWordCompleter(sorted(VALID_TIMEZONES))

_FIELD_VALIDATORS: dict[str, Callable[[str], str]] = {
    "telegram_chat_id": validate_telegram_chat_id,
    "phone": validate_phone,
    "email": validate_email,
    "timezone": validate_timezone,
}


def _prompt_field(field_name: str, display: str) -> str:
    """Prompt for a field value, using fuzzy completion for timezone."""
    prompt_text = f"  {field_name} [{display}]: "
    if field_name == "timezone":
        return pt_prompt(prompt_text, completer=_TZ_COMPLETER)
    return pt_prompt(prompt_text)


def _validate_field(field_name: str, value: str) -> str:
    """Validate a field value. Returns the validated value or raises ValueError."""
    validator = _FIELD_VALIDATORS.get(field_name)
    if validator is not None:
        return validator(value)
    return value


# --- Interactive Dashboard ---


class _DashboardMode(StrEnum):
    CONVERSATIONS = "conversations"
    CONTACTS = "contacts"


@dataclass
class _DashboardState:
    mode: _DashboardMode = _DashboardMode.CONVERSATIONS
    conversations: list[dict[str, object]] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    selected_index: int = 0
    selected_detail: dict[str, object] | None = None
    selected_contact: Contact | None = None
    selected_approvals: list[Approval] = field(default_factory=list)
    tz_name: str = "UTC"
    detail_focused: bool = False
    detail_scroll_offset: int = 0
    confirm_delete: bool = False


async def _refresh_state(state: _DashboardState, db: aiosqlite.Connection) -> None:
    """Fetch conversations, contacts, and approvals, then load detail for selected."""
    await store.cleanup_expired(db)
    state.conversations = await store.get_live_conversations(db)
    state.contacts = await store.list_contacts(db)
    state.approvals = await store.get_pending_approvals(db)
    items = state.contacts if state.mode == _DashboardMode.CONTACTS else state.conversations
    if items:
        state.selected_index = min(state.selected_index, len(items) - 1)
    else:
        state.selected_index = 0
    if state.mode == _DashboardMode.CONTACTS:
        _load_selected_contact(state)
    else:
        await _load_selected_detail(state, db)


async def _load_selected_detail(state: _DashboardState, db: aiosqlite.Connection) -> None:
    """Load conversation messages and per-conversation approvals for the selected item."""
    if not state.conversations:
        state.selected_detail = None
        state.selected_approvals = []
        return
    conv = state.conversations[state.selected_index]
    cid = ConversationId(str(conv["conversation_id"]))
    state.selected_detail = await store.get_conversation(db, cid)
    state.selected_approvals = await store.get_pending_approvals_for_conversation(db, cid)


def _load_selected_contact(state: _DashboardState) -> None:
    """Load contact detail for the selected item in contacts mode."""
    if not state.contacts:
        state.selected_contact = None
        state.selected_approvals = []
        return
    state.selected_contact = state.contacts[state.selected_index]
    state.selected_approvals = []


def _render_conversation_list(state: _DashboardState) -> FormattedText:
    """Render the left pane: selectable conversation list."""
    fragments: list[tuple[str, str]] = []
    header_style = "bold dim" if not state.detail_focused else "dim"
    fragments.append((header_style, " CONVERSATIONS\n"))

    if not state.conversations:
        fragments.append(("italic", " No active conversations.\n"))
        return FormattedText(fragments)

    for i, conv in enumerate(state.conversations):
        is_selected = i == state.selected_index
        cid = str(conv["conversation_id"])
        status = str(conv["status"])
        updated = _format_local_time(str(conv["updated_at"]), state.tz_name)

        has_approval = conv.get("approval_id") is not None
        marker = "!" if has_approval else " "

        is_awaiting = status == ConversationStatus.AWAITING_APPROVAL
        color = "fg:ansiyellow" if is_awaiting else "fg:ansigreen"
        label = "await" if is_awaiting else "active"
        base = "bold " if is_selected else ""

        fragments.append((base + "dim", f" {marker} {updated} "))
        fragments.append((base + color, f"{label:<6s} {cid}"))
        fragments.append(("", "\n"))

    return FormattedText(fragments)


def _render_contacts_list(state: _DashboardState) -> FormattedText:
    """Render the left pane: selectable contacts list."""
    fragments: list[tuple[str, str]] = []
    header_style = "bold dim" if not state.detail_focused else "dim"
    fragments.append((header_style, " CONTACTS\n"))

    if not state.contacts:
        fragments.append(("italic", " No contacts.\n"))
        return FormattedText(fragments)

    for i, contact in enumerate(state.contacts):
        is_selected = i == state.selected_index
        name = contact.name
        identifier = str(contact.telegram_chat_id or contact.phone or contact.email or "\u2014")
        base = "bold " if is_selected else ""
        fragments.append((base, f" {name}  "))
        fragments.append((base + "dim", identifier))
        fragments.append(("", "\n"))

    return FormattedText(fragments)


def _render_left_pane(state: _DashboardState) -> FormattedText:
    """Dispatch left pane rendering based on mode."""
    if state.mode == _DashboardMode.CONTACTS:
        return _render_contacts_list(state)
    return _render_conversation_list(state)


def _render_contact_detail(state: _DashboardState) -> FormattedText:
    """Render the right top pane for contacts mode: contact key/value detail."""
    fragments: list[tuple[str, str]] = []
    header_style = "bold dim" if state.detail_focused else "dim"
    fragments.append((header_style, " DETAIL\n"))

    if state.selected_contact is None:
        fragments.append(("italic", " Select a contact.\n"))
        return FormattedText(fragments)

    contact = state.selected_contact
    for key in ("contact_id", "name", "telegram_chat_id", "phone", "email", "timezone", "notes"):
        value = getattr(contact, key)
        display = str(value) if value is not None else "\u2014"
        fragments.append(("dim", f" {key}: "))
        fragments.append(("", f"{display}\n"))

    return FormattedText(fragments)


def _render_conversation_detail(state: _DashboardState) -> FormattedText:
    """Render the right top pane: conversation info header + scrollable message history."""
    fragments: list[tuple[str, str]] = []
    header_style = "bold dim" if state.detail_focused else "dim"
    fragments.append((header_style, " DETAIL\n"))

    if state.selected_detail is None:
        fragments.append(("italic", " Select a conversation.\n"))
        return FormattedText(fragments)

    # Conversation metadata subheading
    selected_conv = state.conversations[state.selected_index] if state.conversations else None
    if selected_conv is not None:
        updated = _format_local_time(str(selected_conv["updated_at"]), state.tz_name)
        expires = _format_relative_time(
            str(selected_conv["expires_at"]) if selected_conv["expires_at"] else None
        )
        msg_count = selected_conv.get("message_count", 0)
        req_count = selected_conv.get("total_requests", 0)
        activity = _format_activity(
            msg_count if isinstance(msg_count, int) else 0,
            req_count if isinstance(req_count, int) else 0,
        )
        fragments.append(("dim", " updated "))
        fragments.append(("dim fg:ansicyan", updated))
        fragments.append(("dim", "  expires "))
        fragments.append(("dim fg:ansiyellow", expires))
        fragments.append(("dim", f"  {activity}\n"))

    fragments.append(("", "\n"))

    # Build message lines with timestamps, then apply scroll offset
    msg_lines: list[list[tuple[str, str]]] = []
    messages_raw = state.selected_detail.get("messages", "[]")
    raw_messages = json.loads(str(messages_raw))
    messages = [Message.from_dict(d) for d in raw_messages]

    for msg in messages:
        ts = _format_local_time(msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"), state.tz_name)
        if isinstance(msg.content, str):
            style = "fg:ansiblue" if msg.role == "assistant" else "fg:ansigreen"
            msg_lines.append(
                [
                    ("dim", f" {ts} "),
                    (style, f"[{msg.role}] {msg.content}\n"),
                ]
            )
        elif isinstance(msg.content, list):
            for block in msg.content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    style = "fg:ansiblue" if msg.role == "assistant" else "fg:ansigreen"
                    msg_lines.append(
                        [
                            ("dim", f" {ts} "),
                            (style, f"[{msg.role}] {block.get('text', '')}\n"),
                        ]
                    )
                elif block_type == "tool_use":
                    name = block.get("name", "?")
                    inp = json.dumps(block.get("input", {}))
                    msg_lines.append(
                        [
                            ("dim", f" {ts} "),
                            ("fg:ansicyan", f"[tool] {name}({inp})\n"),
                        ]
                    )
                elif block_type == "tool_result":
                    result_content = str(block.get("content", ""))[:100]
                    msg_lines.append(
                        [
                            ("dim", f" {ts} "),
                            ("fg:ansicyan italic", f"[result] {result_content}\n"),
                        ]
                    )

    if not msg_lines:
        fragments.append(("italic", " No messages.\n"))
    else:
        # Clamp scroll so the last message always stays on screen
        max_offset = max(0, len(msg_lines) - 1)
        state.detail_scroll_offset = min(state.detail_scroll_offset, max_offset)
        for line_fragments in msg_lines[state.detail_scroll_offset :]:
            fragments.extend(line_fragments)

    return FormattedText(fragments)


def _render_detail_pane(state: _DashboardState) -> FormattedText:
    """Dispatch detail pane rendering based on mode."""
    if state.mode == _DashboardMode.CONTACTS:
        return _render_contact_detail(state)
    return _render_conversation_detail(state)


def _render_approval(state: _DashboardState) -> FormattedText:
    """Render the conditional approval pane (right bottom)."""
    fragments: list[tuple[str, str]] = []

    if not state.selected_approvals:
        return FormattedText(fragments)

    fragments.append(("bold fg:ansiyellow", " PENDING APPROVAL\n"))
    for appr in state.selected_approvals:
        fragments.append(("", f' "{appr.request_description}"\n'))
        fragments.append(("fg:ansicyan", f" Tool: {appr.tool_name}\n"))
        if isinstance(appr.tool_input, dict):
            for key, value in appr.tool_input.items():
                fragments.append(("dim", f"   {key}: "))
                fragments.append(("", f"{value}\n"))
    fragments.append(("bold", " [a]pprove  [d]eny\n"))

    return FormattedText(fragments)


def _render_status_bar(state: _DashboardState) -> FormattedText:
    """Render the bottom status bar."""
    bg = "bg:ansidarkgray"
    bold = f"{bg} fg:ansiwhite bold"
    normal = f"{bg} fg:ansiwhite"
    dim = f"{bg} fg:ansigray"
    n_conv = len(state.conversations)
    n_pending = len(state.approvals)
    n_contacts = len(state.contacts)

    if state.confirm_delete:
        if state.mode == _DashboardMode.CONTACTS and state.contacts:
            item = state.contacts[state.selected_index].name
        elif state.mode == _DashboardMode.CONVERSATIONS and state.conversations:
            item = str(state.conversations[state.selected_index]["conversation_id"])
        else:
            item = "item"
        return FormattedText([(normal, f" Delete {item}? "), (dim, "x:confirm esc:cancel ")])

    fragments: list[tuple[str, str]] = []

    def _count(n: int, label: str) -> None:
        fragments.append((bold, f" {n}"))
        suffix = "s" if n != 1 else ""
        fragments.append((normal, f" {label}{suffix}"))

    def _sep() -> None:
        fragments.append((normal, " |"))

    if state.mode == _DashboardMode.CONTACTS:
        _count(n_contacts, "contact")
        _sep()
        _count(n_conv, "convo")
        _sep()
        _count(n_pending, "pending")
        fragments.append((dim, " | c:conversations x:delete q:quit "))
    elif state.detail_focused:
        _count(n_conv, "convo")
        _sep()
        _count(n_pending, "pending")
        _sep()
        _count(n_contacts, "contact")
        fragments.append((dim, " | esc:back x:delete q:quit "))
    else:
        _count(n_conv, "convo")
        _sep()
        _count(n_pending, "pending")
        _sep()
        _count(n_contacts, "contact")
        fragments.append((dim, " | c:contacts enter:detail x:delete q:quit "))

    return FormattedText(fragments)


def _make_key_bindings(
    state: _DashboardState,
    db: aiosqlite.Connection,
    app_ref: list[Application[None]],
) -> KeyBindings:
    """Create key bindings that mutate state and invalidate the app."""
    kb = KeyBindings()
    # Keep references to background tasks so they aren't garbage-collected
    bg_tasks: set[asyncio.Task[None]] = set()

    def _schedule(coro: Coroutine[object, object, None]) -> None:
        task = asyncio.ensure_future(coro)
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)

    async def _nav_and_refresh(delta: int) -> None:
        items = state.contacts if state.mode == _DashboardMode.CONTACTS else state.conversations
        if not items:
            return
        state.selected_index = max(0, min(len(items) - 1, state.selected_index + delta))
        state.detail_scroll_offset = 0
        if state.mode == _DashboardMode.CONTACTS:
            _load_selected_contact(state)
        else:
            await _load_selected_detail(state, db)
        app_ref[0].invalidate()

    @kb.add("down")
    def _next(_event: object) -> None:
        if state.detail_focused:
            state.detail_scroll_offset += 1
            app_ref[0].invalidate()
        else:
            _schedule(_nav_and_refresh(1))

    @kb.add("up")
    def _prev(_event: object) -> None:
        if state.detail_focused:
            state.detail_scroll_offset = max(0, state.detail_scroll_offset - 1)
            app_ref[0].invalidate()
        else:
            _schedule(_nav_and_refresh(-1))

    @kb.add("enter")
    def _enter_detail(_event: object) -> None:
        if (
            state.mode == _DashboardMode.CONVERSATIONS
            and not state.detail_focused
            and state.selected_detail is not None
        ):
            state.detail_focused = True
            app_ref[0].invalidate()

    @kb.add("escape")
    def _exit_detail(_event: object) -> None:
        if state.confirm_delete:
            state.confirm_delete = False
            app_ref[0].invalidate()
        elif state.detail_focused:
            state.detail_focused = False
            state.detail_scroll_offset = 0
            app_ref[0].invalidate()

    @kb.add("c")
    def _toggle_mode(_event: object) -> None:
        if state.mode == _DashboardMode.CONVERSATIONS:
            state.mode = _DashboardMode.CONTACTS
        else:
            state.mode = _DashboardMode.CONVERSATIONS
        state.selected_index = 0
        state.detail_focused = False
        state.detail_scroll_offset = 0
        state.confirm_delete = False

        async def _do() -> None:
            if state.mode == _DashboardMode.CONTACTS:
                _load_selected_contact(state)
            else:
                await _load_selected_detail(state, db)
            app_ref[0].invalidate()

        _schedule(_do())

    @kb.add("x")
    def _delete(_event: object) -> None:
        if not state.confirm_delete:
            items = state.contacts if state.mode == _DashboardMode.CONTACTS else state.conversations
            if items:
                state.confirm_delete = True
                app_ref[0].invalidate()
            return

        async def _do() -> None:
            if state.mode == _DashboardMode.CONTACTS and state.contacts:
                contact = state.contacts[state.selected_index]
                await store.delete_contact(db, contact.contact_id)
            elif state.mode == _DashboardMode.CONVERSATIONS and state.conversations:
                conv = state.conversations[state.selected_index]
                await store.delete_conversation(db, ConversationId(str(conv["conversation_id"])))
            state.confirm_delete = False
            await _refresh_state(state, db)
            app_ref[0].invalidate()

        _schedule(_do())

    @kb.add("a")
    def _approve(_event: object) -> None:
        if state.mode != _DashboardMode.CONVERSATIONS:
            return

        async def _do() -> None:
            if not state.selected_approvals:
                return
            appr = state.selected_approvals[0]
            await store.resolve_approval(db, appr.id, ApprovalStatus.APPROVED)
            await _refresh_state(state, db)
            app_ref[0].invalidate()

        _schedule(_do())

    @kb.add("d")
    def _deny(_event: object) -> None:
        if state.mode != _DashboardMode.CONVERSATIONS:
            return

        async def _do() -> None:
            if not state.selected_approvals:
                return
            appr = state.selected_approvals[0]
            await store.resolve_approval(db, appr.id, ApprovalStatus.DENIED)
            await _refresh_state(state, db)
            app_ref[0].invalidate()

        _schedule(_do())

    @kb.add("q")
    @kb.add("c-c")
    def _quit(_event: object) -> None:
        app_ref[0].exit()

    return kb


async def _auto_refresh_loop(
    state: _DashboardState,
    db: aiosqlite.Connection,
    app_ref: list[Application[None]],
    interval: int,
) -> None:
    """Periodically refresh state in the background."""
    while True:
        await asyncio.sleep(interval)
        await _refresh_state(state, db)
        app_ref[0].invalidate()


async def run_dashboard(config: AdminConfig) -> None:
    """Owner dashboard: interactive two-pane TUI for viewing conversations and approving requests."""
    db = await open_store(config.storage.db_path)
    log.info("cli_owner_started")

    state = _DashboardState(tz_name=config.owner_timezone)
    await _refresh_state(state, db)

    # We use a list to hold the app reference so key bindings can access it
    app_ref: list[Application[None]] = []

    list_control = FormattedTextControl(lambda: _render_left_pane(state))
    detail_control = FormattedTextControl(lambda: _render_detail_pane(state))
    approval_control = FormattedTextControl(lambda: _render_approval(state))
    status_control = FormattedTextControl(lambda: _render_status_bar(state))

    left_pane = Window(content=list_control, width=40, wrap_lines=True)
    detail_pane = Window(content=detail_control, wrap_lines=True)
    approval_pane = Window(content=approval_control, dont_extend_height=True, wrap_lines=True)
    status_bar = Window(content=status_control, height=1, style="bg:ansidarkgray")

    # Vertical separator
    separator = Window(width=1, char="\u2502")

    body = VSplit(
        [
            left_pane,
            separator,
            HSplit(
                [
                    detail_pane,
                    approval_pane,
                ]
            ),
        ]
    )

    root = HSplit([body, status_bar])
    layout = Layout(root)

    kb = _make_key_bindings(state, db, app_ref)

    application: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        mouse_support=False,
    )
    application.ttimeoutlen = 0.1
    app_ref.append(application)

    refresh_task = asyncio.ensure_future(
        _auto_refresh_loop(state, db, app_ref, OWNER_REFRESH_SECONDS)
    )

    try:
        await application.run_async()
    finally:
        refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_task
        await db.close()


# --- Non-dashboard admin commands ---


async def run_contacts_list(config: AdminConfig) -> None:
    """List all contacts."""
    console = Console()
    db = await open_store(config.storage.db_path)
    try:
        contacts = await store.list_contacts(db)
        if not contacts:
            console.print("No contacts found.", style="dim")
            return

        table = Table(
            box=None, show_header=True, header_style="bold dim", pad_edge=False, padding=(0, 2)
        )
        table.add_column("ID")
        table.add_column("Name")
        table.add_column("Telegram")
        table.add_column("Phone")
        table.add_column("Email")
        table.add_column("Timezone")
        table.add_column("Notes")

        for c in contacts:
            table.add_row(
                str(c.contact_id)[:8],
                c.name,
                str(c.telegram_chat_id or "\u2014"),
                str(c.phone or "\u2014"),
                str(c.email or "\u2014"),
                str(c.timezone or "\u2014"),
                str(c.notes or "\u2014"),
            )

        console.print(table)
    finally:
        await db.close()


async def run_contacts_add(
    config: AdminConfig,
    contact_id: str,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    timezone: str | None = None,
    notes: str | None = None,
    telegram_chat_id: str | None = None,
) -> None:
    """Add a new contact."""
    console = Console()
    db = await open_store(config.storage.db_path)
    try:
        if phone is None and email is None and telegram_chat_id is None:
            console.print(
                "Warning: contact has no phone, email, or telegram_chat_id.",
                style="bold yellow",
            )

        # Validate fields
        try:
            if phone is not None:
                phone = validate_phone(phone)
            if email is not None:
                email = validate_email(email)
            if timezone is not None:
                timezone = validate_timezone(timezone)
        except ValueError as e:
            console.print(f"Error: {e}", style="red")
            return

        cid = ContactId(contact_id)
        existing = await store.get_contact(db, cid)
        if existing is not None:
            console.print(f"Error: contact_id '{contact_id}' already exists.", style="red")
            return

        await store.create_contact(
            db,
            contact_id=cid,
            name=name,
            phone=phone,
            email=email,
            timezone=timezone,
            notes=notes,
            telegram_chat_id=telegram_chat_id,
        )
        console.print(f"Created contact: {contact_id}", style="green")
    finally:
        await db.close()


async def run_contacts_edit(config: AdminConfig, contact_id: str) -> None:
    """Edit an existing contact. Uses fuzzy finder for timezone selection."""
    console = Console()
    db = await open_store(config.storage.db_path)
    try:
        contact = await store.get_contact(db, ContactId(contact_id))
        if contact is None:
            console.print(f"Contact not found: {contact_id}", style="red")
            return

        console.print(f"Editing contact: {contact.name}", style="bold")
        console.print("Press Enter to keep current value, '-' to clear.", style="dim")
        console.print("Timezone field has fuzzy search \u2014 start typing to filter.", style="dim")

        fields: dict[str, str | None] = {}
        for field_name in ("name", "telegram_chat_id", "phone", "email", "timezone", "notes"):
            current = getattr(contact, field_name)
            display = str(current) if current else "(none)"

            value = await asyncio.to_thread(_prompt_field, field_name, display)
            value = value.strip()

            if value == "-":
                if field_name == "name":
                    console.print("  Cannot clear name.", style="red")
                else:
                    fields[field_name] = None
            elif value:
                try:
                    value = _validate_field(field_name, value)
                except ValueError as e:
                    console.print(f"  {e}", style="red")
                    continue
                fields[field_name] = value

        if fields:
            updated = await store.update_contact(db, ContactId(contact_id), fields)
            if updated:
                console.print("Contact updated.", style="green")
            else:
                console.print("No changes made.", style="dim")
        else:
            console.print("No changes.", style="dim")
    finally:
        await db.close()


async def run_contacts_rm(config: AdminConfig, contact_id: str) -> None:
    """Delete a contact."""
    console = Console()
    db = await open_store(config.storage.db_path)
    try:
        deleted = await store.delete_contact(db, ContactId(contact_id))
        if deleted:
            console.print(f"Deleted contact: {contact_id}", style="green")
        else:
            console.print(f"Contact not found: {contact_id}", style="red")
    finally:
        await db.close()


async def run_audit_log(config: AdminConfig, conversation_id: str | None = None) -> None:
    """Display audit log entries."""
    console = Console()
    db = await open_store(config.storage.db_path)
    try:
        cid = ConversationId(conversation_id) if conversation_id else None
        entries = await store.get_audit_log(db, conversation_id=cid)

        if not entries:
            console.print("No audit log entries.", style="dim")
            return

        table = Table(
            box=None, show_header=True, header_style="bold dim", pad_edge=False, padding=(0, 2)
        )
        table.add_column("Timestamp")
        table.add_column("Action")
        table.add_column("Conversation")
        table.add_column("Details")

        for entry in entries:
            details = entry.get("details", {})
            details_str = json.dumps(details)[:60] if details else "\u2014"
            table.add_row(
                str(entry["timestamp"] or "\u2014"),
                str(entry["action_type"]),
                str(entry["conversation_id"] or "\u2014"),
                details_str,
            )

        console.print(table)
    finally:
        await db.close()


async def run_conversations_list(config: AdminConfig) -> None:
    """List active conversations."""
    console = Console()
    db = await open_store(config.storage.db_path)
    try:
        live_convs = await store.get_live_conversations(db)
        if not live_convs:
            console.print("No active conversations.", style="dim")
            return
        console.print(_build_owner_table(live_convs, config.owner_timezone))
    finally:
        await db.close()


async def run_conversation_detail(config: AdminConfig, conversation_id: str) -> None:
    """Display message history for a conversation."""
    console = Console()
    db = await open_store(config.storage.db_path)
    try:
        cid = ConversationId(conversation_id)
        conv = await store.get_conversation(db, cid)
        if conv is None:
            console.print(f"Conversation not found: {conversation_id}", style="red")
            return

        console.print(f"Conversation: {conversation_id}", style="bold")
        console.print(f"Status: {conv['status']}", style="dim")
        console.print()

        raw_messages = json.loads(str(conv["messages"]))
        for msg in [Message.from_dict(d) for d in raw_messages]:
            ts = msg.timestamp.strftime("%H:%M")
            if isinstance(msg.content, str):
                style = "blue" if msg.role == "assistant" else "green"
                console.print(f"{ts} [{msg.role}] {msg.content}", style=style)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            style = "blue" if msg.role == "assistant" else "green"
                            console.print(f"{ts} [{msg.role}] {block['text']}", style=style)
                        elif block.get("type") == "tool_use":
                            console.print(
                                f"{ts} [tool_use] {block.get('name', '?')}({json.dumps(block.get('input', {}))})",
                                style="cyan",
                            )
                        elif block.get("type") == "tool_result":
                            result_content = block.get("content", "")
                            console.print(
                                f"{ts} [tool_result] {str(result_content)[:100]}",
                                style="dim cyan",
                            )
    finally:
        await db.close()
