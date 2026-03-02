from unittest.mock import AsyncMock

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
from homunculus.server.admin import (
    ContactResponse,
    ConversationDetail,
    ConversationSummary,
    MessageItem,
    OwnerRequestResponse,
)


def _make_conv_summary(**overrides: object) -> ConversationSummary:
    defaults = {
        "conversation_id": "telegram:alice",
        "status": "active",
        "updated_at": "2025-01-01 14:30:00",
        "expires_at": None,
        "message_count": 0,
        "total_requests": 0,
        "request_id": None,
        "request_description": None,
    }
    defaults.update(overrides)
    return ConversationSummary(**defaults)


def _make_contact_response(**overrides: object) -> ContactResponse:
    defaults = {
        "contact_id": "alice",
        "name": "Alice",
        "phone": None,
        "email": None,
        "timezone": None,
        "notes": None,
        "telegram_chat_id": "111111111",
    }
    defaults.update(overrides)
    return ContactResponse(**defaults)


def _make_request_response(**overrides: object) -> OwnerRequestResponse:
    defaults = {
        "id": "abc",
        "conversation_id": "telegram:123",
        "request_type": "approval",
        "description": "Create lunch event",
        "tool_name": "create_event",
        "tool_input": {},
        "options": None,
        "status": "pending",
        "created_at": "2025-01-01 12:00:00",
        "resolved_at": None,
        "response_text": None,
    }
    defaults.update(overrides)
    return OwnerRequestResponse(**defaults)


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
            _make_conv_summary(conversation_id="telegram:alice"),
            _make_conv_summary(
                conversation_id="telegram:bob",
                status="awaiting_owner",
                updated_at="2025-01-01 14:25:00",
                request_id="abc123",
            ),
        ],
        selected_index=0,
    )
    result = _render_conversation_list(state)
    text = "".join(frag[1] for frag in result)
    assert "telegram:alice" in text
    assert "telegram:bob" in text
    assert "!" in text  # request marker for bob


def test_render_conversation_list_selection_highlight():
    state = _DashboardState(
        tz_name="UTC",
        conversations=[
            _make_conv_summary(conversation_id="telegram:alice"),
            _make_conv_summary(conversation_id="telegram:bob", updated_at="2025-01-01 14:25:00"),
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
            "messages": [
                {"role": "user", "content": "Hello", "timestamp": "1970-01-01 00:00:00"},
                {
                    "role": "assistant",
                    "content": "Hi there!",
                    "timestamp": "1970-01-01 00:00:00",
                },
            ],
        },
    )
    result = _render_conversation_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "[user] Hello" in text
    assert "[assistant] Hi there!" in text


def test_render_conversation_detail_with_tool_use():
    state = _DashboardState(
        selected_detail={
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "create_event",
                            "input": {"summary": "Lunch"},
                        },
                    ],
                    "timestamp": "1970-01-01 00:00:00",
                },
            ],
        },
    )
    result = _render_conversation_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "[tool] create_event" in text
    assert "Lunch" in text


def test_render_approval_with_pending():
    state = _DashboardState(
        selected_approvals=[_make_request_response()],
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
            _make_request_response(tool_input={"summary": "Lunch", "start": "2025-01-01T12:00:00"}),
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
            "messages": [
                {"role": "user", "content": "Hello", "timestamp": "2025-01-01 14:30:00"},
                {
                    "role": "assistant",
                    "content": "Hi there!",
                    "timestamp": "2025-01-01 14:30:05",
                },
            ],
        },
    )
    result = _render_conversation_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "14:30" in text


def test_render_conversation_detail_handles_missing_timestamps():
    state = _DashboardState(
        selected_detail={
            "messages": [
                {
                    "role": "user",
                    "content": "Hello",
                    "timestamp": "1970-01-01 00:00:00",
                },
            ],
        },
    )
    result = _render_conversation_detail(state)
    text = "".join(frag[1] for frag in result)
    assert "[user] Hello" in text


def test_render_status_bar_with_data():
    state = _DashboardState(
        conversations=[_make_conv_summary(), _make_conv_summary(conversation_id="telegram:bob")],
        approvals=[_make_request_response()],
    )
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "2 convos" in text
    assert "1 pending" in text


# --- State management ---


async def test_refresh_state():
    client = AsyncMock()
    client.list_conversations = AsyncMock(return_value=[])
    client.list_contacts = AsyncMock(return_value=[])
    client.list_requests = AsyncMock(return_value=[])

    state = _DashboardState(tz_name="UTC")
    await _refresh_state(state, client)
    assert state.conversations == []
    assert state.approvals == []
    assert state.selected_detail is None


async def test_refresh_state_with_conversations():
    conv = _make_conv_summary(conversation_id="telegram:111111111")
    detail = ConversationDetail(
        conversation_id="telegram:111111111",
        status="active",
        created_at="2025-01-01 00:00:00",
        updated_at="2025-01-01 14:30:00",
        expires_at=None,
        messages=[MessageItem(role="user", content="hi", timestamp="2025-01-01 14:30:00")],
    )

    client = AsyncMock()
    client.list_conversations = AsyncMock(return_value=[conv])
    client.list_contacts = AsyncMock(return_value=[])
    client.list_requests = AsyncMock(return_value=[])
    client.get_conversation = AsyncMock(return_value=detail)

    state = _DashboardState(tz_name="UTC")
    await _refresh_state(state, client)

    assert len(state.conversations) == 1
    assert state.selected_detail is not None


async def test_refresh_state_clamps_selected_index():
    client = AsyncMock()
    client.list_conversations = AsyncMock(return_value=[])
    client.list_contacts = AsyncMock(return_value=[])
    client.list_requests = AsyncMock(return_value=[])

    state = _DashboardState(tz_name="UTC", selected_index=10)
    await _refresh_state(state, client)
    assert state.selected_index == 0


async def test_load_selected_detail_with_requests():
    conv = _make_conv_summary(conversation_id="telegram:111111111")
    detail = ConversationDetail(
        conversation_id="telegram:111111111",
        status="active",
        created_at="2025-01-01 00:00:00",
        updated_at="2025-01-01 14:30:00",
        expires_at=None,
        messages=[],
    )
    req = _make_request_response(
        conversation_id="telegram:111111111",
        description="Create event",
        tool_name="create_event",
    )

    client = AsyncMock()
    client.get_conversation = AsyncMock(return_value=detail)

    state = _DashboardState(
        tz_name="UTC",
        conversations=[conv],
        approvals=[req],
        selected_index=0,
    )
    await _load_selected_detail(state, client)

    assert state.selected_detail is not None
    assert len(state.selected_approvals) == 1
    assert state.selected_approvals[0].description == "Create event"


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
            _make_contact_response(contact_id="a", name="Alice", telegram_chat_id="111111111"),
            _make_contact_response(
                contact_id="b", name="Bob", email="bob@example.com", telegram_chat_id=None
            ),
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
            _make_contact_response(contact_id="a", name="Alice", telegram_chat_id="111111111"),
            _make_contact_response(
                contact_id="b", name="Bob", email="bob@example.com", telegram_chat_id=None
            ),
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
        selected_contact=_make_contact_response(
            contact_id="alice",
            name="Alice",
            telegram_chat_id="111111111",
            phone="+11111111111",
            email="alice@example.com",
            timezone="US/Eastern",
            notes="A friend",
        ),
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
            _make_contact_response(contact_id="alice", name="Alice", telegram_chat_id="111"),
            _make_contact_response(
                contact_id="bob", name="Bob", email="bob@x.com", telegram_chat_id=None
            ),
        ],
        selected_index=1,
    )
    _load_selected_contact(state)
    assert state.selected_contact is not None
    assert state.selected_contact.name == "Bob"
    assert state.selected_approvals == []


def test_load_selected_contact_empty():
    state = _DashboardState(mode=_DashboardMode.CONTACTS)
    _load_selected_contact(state)
    assert state.selected_contact is None
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
        contacts=[_make_contact_response(contact_id="a", name="Alice")],
    )
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "1 contact" in text
    assert "c:conversations" in text


def test_status_bar_conversations_mode():
    state = _DashboardState(
        conversations=[_make_conv_summary()],
        contacts=[
            _make_contact_response(contact_id="a", name="A"),
            _make_contact_response(contact_id="b", name="B"),
        ],
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
        conversations=[_make_conv_summary(conversation_id="telegram:alice")],
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
        contacts=[_make_contact_response(contact_id="a", name="Alice")],
        selected_index=0,
    )
    result = _render_status_bar(state)
    text = "".join(frag[1] for frag in result)
    assert "Delete Alice?" in text
    assert "x:confirm" in text


# --- Contacts mode refresh ---


async def test_refresh_state_contacts_mode():
    client = AsyncMock()
    client.list_conversations = AsyncMock(return_value=[])
    client.list_contacts = AsyncMock(
        return_value=[
            _make_contact_response(contact_id="alice", name="Alice", phone="+11111111111")
        ]
    )
    client.list_requests = AsyncMock(return_value=[])

    state = _DashboardState(tz_name="UTC", mode=_DashboardMode.CONTACTS)
    await _refresh_state(state, client)
    assert len(state.contacts) == 1
    assert state.selected_contact is not None
    assert state.selected_contact.name == "Alice"


async def test_refresh_state_contacts_mode_clamps_index():
    client = AsyncMock()
    client.list_conversations = AsyncMock(return_value=[])
    client.list_contacts = AsyncMock(return_value=[])
    client.list_requests = AsyncMock(return_value=[])

    state = _DashboardState(tz_name="UTC", mode=_DashboardMode.CONTACTS, selected_index=10)
    await _refresh_state(state, client)
    assert state.selected_index == 0
