import json
from datetime import UTC, datetime, timedelta

from homunculus.cli.admin import (
    _DashboardMode,
    _DashboardState,
    _load_selected_contact,
    _load_selected_detail,
    _refresh_state,
    _render_approval,
    _render_contact_detail,
    _render_contacts_list,
    _render_conversation_detail,
    _render_conversation_list,
    _render_status_bar,
)
from homunculus.storage import store
from homunculus.types import ContactId, ConversationId

# --- _DashboardState defaults ---


def test_dashboard_state_defaults():
    state = _DashboardState()
    assert state.mode == "conversations"
    assert state.conversations == []
    assert state.contacts == []
    assert state.approvals == []
    assert state.selected_index == 0
    assert state.selected_detail is None
    assert state.selected_approvals == []
    assert state.tz_name == "UTC"
    assert state.detail_focused is False
    assert state.detail_scroll_offset == 0
    assert state.confirm_delete is False


# --- Rendering with empty state ---


def test_render_conversation_list_empty():
    state = _DashboardState()
    result = _render_conversation_list(state)
    text = "".join(frag[1] for frag in result)
    assert "No active conversations" in text


def test_render_conversation_detail_no_selection():
    state = _DashboardState()
    result = _render_conversation_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "Select a conversation" in text


def test_render_approval_empty():
    state = _DashboardState()
    result = _render_approval(state)
    assert len(list(result)) == 0


def test_render_status_bar_empty():
    state = _DashboardState()
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "0 convo" in text
    assert "0 pending" in text


# --- Rendering with populated state ---


def test_render_conversation_list_with_data():
    state = _DashboardState(
        tz_name="UTC",
        conversations=[
            {
                "conversation_id": "telegram:alice",
                "status": "active",
                "updated_at": "2025-01-01 14:30:00",
                "expires_at": None,
                "approval_id": None,
            },
            {
                "conversation_id": "telegram:bob",
                "status": "awaiting_approval",
                "updated_at": "2025-01-01 14:25:00",
                "expires_at": None,
                "approval_id": "abc123",
            },
        ],
        selected_index=0,
    )
    result = _render_conversation_list(state)
    text = "".join(frag[1] for frag in result)
    assert "telegram:alice" in text
    assert "telegram:bob" in text
    assert "!" in text  # approval marker for bob


def test_render_conversation_list_selection_highlight():
    state = _DashboardState(
        tz_name="UTC",
        conversations=[
            {
                "conversation_id": "telegram:alice",
                "status": "active",
                "updated_at": "2025-01-01 14:30:00",
                "expires_at": None,
                "approval_id": None,
            },
            {
                "conversation_id": "telegram:bob",
                "status": "active",
                "updated_at": "2025-01-01 14:25:00",
                "expires_at": None,
                "approval_id": None,
            },
        ],
        selected_index=1,
    )
    result = _render_conversation_list(state)
    fragments = list(result)
    # The selected item (index 1 = bob) should have bold in its style
    bold_fragments = [f for f in fragments if "bold" in f[0] and "telegram:" in f[1]]
    assert len(bold_fragments) == 1
    assert "telegram:bob" in bold_fragments[0][1]


def test_render_conversation_detail_with_messages():
    state = _DashboardState(
        selected_detail={
            "messages": json.dumps(
                [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ]
            ),
        },
    )
    result = _render_conversation_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "[user] Hello" in text
    assert "[assistant] Hi there!" in text


def test_render_conversation_detail_with_tool_use():
    state = _DashboardState(
        selected_detail={
            "messages": json.dumps(
                [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "create_event",
                                "input": {"summary": "Lunch"},
                            },
                        ],
                    },
                ]
            ),
        },
    )
    result = _render_conversation_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "[tool] create_event" in text
    assert "Lunch" in text


def test_render_approval_with_pending():
    state = _DashboardState(
        selected_approvals=[
            {
                "id": "abc",
                "request_description": "Create lunch event",
                "tool_name": "create_event",
            },
        ],
    )
    result = _render_approval(state)
    text = "".join(frag[1] for frag in result)
    assert "PENDING APPROVAL" in text
    assert "Create lunch event" in text
    assert "create_event" in text
    assert "[a]pprove" in text
    assert "[d]eny" in text


def test_render_approval_shows_tool_input():
    state = _DashboardState(
        selected_approvals=[
            {
                "id": "abc",
                "request_description": "Create lunch event",
                "tool_name": "create_event",
                "tool_input": {"summary": "Lunch", "start": "2025-01-01T12:00:00"},
            },
        ],
    )
    result = _render_approval(state)
    text = "".join(frag[1] for frag in result)
    assert "summary:" in text
    assert "Lunch" in text
    assert "start:" in text
    assert "2025-01-01T12:00:00" in text


def test_render_conversation_detail_shows_timestamps():
    state = _DashboardState(
        tz_name="UTC",
        selected_detail={
            "messages": json.dumps(
                [
                    {"role": "user", "content": "Hello", "ts": "2025-01-01 14:30:00"},
                    {"role": "assistant", "content": "Hi there!", "ts": "2025-01-01 14:30:05"},
                ]
            ),
        },
    )
    result = _render_conversation_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "14:30" in text


def test_render_conversation_detail_handles_missing_timestamps():
    state = _DashboardState(
        selected_detail={
            "messages": json.dumps(
                [
                    {"role": "user", "content": "Hello"},
                ]
            ),
        },
    )
    result = _render_conversation_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "[user] Hello" in text


def test_render_status_bar_with_data():
    state = _DashboardState(
        conversations=[{"id": "1"}, {"id": "2"}],
        approvals=[{"id": "a"}],
    )
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "2 convos" in text
    assert "1 pending" in text


# --- State management ---


async def test_refresh_state(db):
    state = _DashboardState(tz_name="UTC")
    await _refresh_state(state, db)
    assert state.conversations == []
    assert state.approvals == []
    assert state.selected_detail is None


async def test_refresh_state_with_conversations(db):

    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    cid = ConversationId("telegram:111111111")
    await store.save_conversation(
        db, cid, json.dumps([{"role": "user", "content": "hi"}]), expires_at=future
    )

    state = _DashboardState(tz_name="UTC")
    await _refresh_state(state, db)

    assert len(state.conversations) == 1
    assert state.selected_detail is not None


async def test_refresh_state_clamps_selected_index(db):
    state = _DashboardState(tz_name="UTC", selected_index=10)
    await _refresh_state(state, db)
    assert state.selected_index == 0


async def test_load_selected_detail_with_approvals(db):

    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    cid = ConversationId("telegram:111111111")
    await store.save_conversation(db, cid, "[]", expires_at=future)
    await store.create_approval(db, cid, "Create event", "create_event", {"s": "test"})

    state = _DashboardState(
        tz_name="UTC",
        conversations=[{"conversation_id": str(cid)}],
        selected_index=0,
    )
    await _load_selected_detail(state, db)

    assert state.selected_detail is not None
    assert len(state.selected_approvals) == 1
    assert state.selected_approvals[0]["request_description"] == "Create event"


# --- Contacts mode ---


def test_render_contacts_list_empty():
    state = _DashboardState(mode=_DashboardMode.CONTACTS)
    result = _render_contacts_list(state)
    text = "".join(frag[1] for frag in result)
    assert "CONTACTS" in text
    assert "No contacts" in text


def test_render_contacts_list_with_data():
    state = _DashboardState(
        mode=_DashboardMode.CONTACTS,
        contacts=[
            {"name": "Alice", "telegram_chat_id": "111111111", "phone": None, "email": None},
            {"name": "Bob", "telegram_chat_id": None, "phone": None, "email": "bob@example.com"},
        ],
        selected_index=0,
    )
    result = _render_contacts_list(state)
    text = "".join(frag[1] for frag in result)
    assert "Alice" in text
    assert "111111111" in text
    assert "Bob" in text
    assert "bob@example.com" in text


def test_render_contacts_list_selection_highlight():
    state = _DashboardState(
        mode=_DashboardMode.CONTACTS,
        contacts=[
            {"name": "Alice", "telegram_chat_id": "111111111", "phone": None, "email": None},
            {"name": "Bob", "telegram_chat_id": None, "phone": None, "email": "bob@example.com"},
        ],
        selected_index=1,
    )
    result = _render_contacts_list(state)
    fragments = list(result)
    bold_fragments = [f for f in fragments if "bold" in f[0] and "Bob" in f[1]]
    assert len(bold_fragments) == 1


def test_render_contact_detail_no_selection():
    state = _DashboardState(mode=_DashboardMode.CONTACTS)
    result = _render_contact_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "Select a contact" in text


def test_render_contact_detail_with_data():
    state = _DashboardState(
        mode=_DashboardMode.CONTACTS,
        selected_detail={
            "contact_id": "alice",
            "name": "Alice",
            "telegram_chat_id": "111111111",
            "phone": "+11111111111",
            "email": "alice@example.com",
            "timezone": "US/Eastern",
            "notes": "A friend",
        },
    )
    result = _render_contact_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "DETAIL" in text
    assert "alice" in text
    assert "Alice" in text
    assert "111111111" in text
    assert "alice@example.com" in text
    assert "US/Eastern" in text
    assert "A friend" in text


def test_load_selected_contact():
    state = _DashboardState(
        mode=_DashboardMode.CONTACTS,
        contacts=[
            {
                "contact_id": "alice",
                "name": "Alice",
                "telegram_chat_id": "111",
                "phone": None,
                "email": None,
            },
            {
                "contact_id": "bob",
                "name": "Bob",
                "telegram_chat_id": None,
                "phone": None,
                "email": "bob@x.com",
            },
        ],
        selected_index=1,
    )
    _load_selected_contact(state)
    assert state.selected_detail is not None
    assert state.selected_detail["name"] == "Bob"
    assert state.selected_approvals == []


def test_load_selected_contact_empty():
    state = _DashboardState(mode=_DashboardMode.CONTACTS)
    _load_selected_contact(state)
    assert state.selected_detail is None
    assert state.selected_approvals == []


# --- Status bar ---


def test_status_bar_styling():
    state = _DashboardState()
    result = _render_status_bar(state)
    fragments = list(result)
    # All fragments should use a bg:ansidarkgray base
    assert all("bg:ansidarkgray" in f[0] for f in fragments)


def test_status_bar_contacts_mode():
    state = _DashboardState(
        mode=_DashboardMode.CONTACTS,
        contacts=[{"name": "Alice"}],
    )
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "1 contact" in text
    assert "c:conversations" in text


def test_status_bar_conversations_mode():
    state = _DashboardState(
        conversations=[{"id": "1"}],
        contacts=[{"name": "A"}, {"name": "B"}],
    )
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "1 convo" in text
    assert "2 contacts" in text
    assert "c:contacts" in text


def test_status_bar_detail_focused():
    state = _DashboardState(detail_focused=True)
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "esc:back" in text


def test_status_bar_confirm_delete_conversation():
    state = _DashboardState(
        confirm_delete=True,
        conversations=[{"conversation_id": "telegram:alice"}],
        selected_index=0,
    )
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "Delete telegram:alice?" in text
    assert "x:confirm" in text
    assert "esc:cancel" in text


def test_status_bar_confirm_delete_contact():
    state = _DashboardState(
        mode=_DashboardMode.CONTACTS,
        confirm_delete=True,
        contacts=[{"name": "Alice"}],
        selected_index=0,
    )
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "Delete Alice?" in text
    assert "x:confirm" in text


# --- Contacts mode refresh ---


async def test_refresh_state_contacts_mode(db):
    await store.create_contact(db, ContactId("alice"), "Alice", phone="+11111111111")
    state = _DashboardState(tz_name="UTC", mode=_DashboardMode.CONTACTS)
    await _refresh_state(state, db)
    assert len(state.contacts) == 1
    assert state.selected_detail is not None
    assert state.selected_detail["name"] == "Alice"


async def test_refresh_state_contacts_mode_clamps_index(db):
    state = _DashboardState(tz_name="UTC", mode=_DashboardMode.CONTACTS, selected_index=10)
    await _refresh_state(state, db)
    assert state.selected_index == 0
