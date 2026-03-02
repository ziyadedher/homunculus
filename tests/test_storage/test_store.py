import json
from datetime import UTC, datetime, timedelta

from homunculus.storage import store
from homunculus.types import ConversationId, ConversationStatus, RequestStatus, RequestType


async def test_conversation_roundtrip(db):
    cid = ConversationId("telegram:123456789")
    messages = '[{"role": "user", "content": "hello"}]'

    await store.save_conversation(db, cid, messages)
    loaded = await store.get_conversation_json(db, cid)
    assert loaded == messages


async def test_conversation_empty(db):
    loaded = await store.get_conversation_json(db, ConversationId("nonexistent"))
    assert loaded == "[]"


async def test_conversation_update(db):
    cid = ConversationId("telegram:123456789")
    await store.save_conversation(db, cid, '[{"role": "user", "content": "first"}]')
    await store.save_conversation(
        db,
        cid,
        '[{"role": "user", "content": "first"}, {"role": "assistant", "content": "second"}]',
    )

    loaded = json.loads(await store.get_conversation_json(db, cid))
    assert len(loaded) == 2


async def test_request_lifecycle(db):
    cid = ConversationId("telegram:123456789")
    request_id = await store.create_request(
        db,
        conversation_id=cid,
        request_type=RequestType.APPROVAL,
        description="Create lunch event",
        tool_name="create_event",
        tool_input={"summary": "Lunch"},
    )
    assert request_id

    pending = await store.get_oldest_pending_request(db)
    assert pending is not None
    assert pending.id == request_id
    assert pending.status == RequestStatus.PENDING

    await store.resolve_request(db, request_id, RequestStatus.APPROVED)

    pending = await store.get_oldest_pending_request(db)
    assert pending is None


async def test_get_pending_requests_empty(db):
    result = await store.get_pending_requests(db)
    assert result == []


async def test_get_pending_requests_multiple(db):
    cid = ConversationId("telegram:123456789")
    id1 = await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"s": "Lunch"},
    )
    id2 = await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Delete meeting",
        tool_name="delete_event",
        tool_input={"id": "123"},
    )

    result = await store.get_pending_requests(db)
    assert len(result) == 2
    assert result[0].id == id1
    assert result[1].id == id2


async def test_get_pending_requests_excludes_resolved(db):
    cid = ConversationId("telegram:123456789")
    id1 = await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"s": "Lunch"},
    )
    await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Delete meeting",
        tool_name="delete_event",
        tool_input={"id": "123"},
    )

    await store.resolve_request(db, id1, RequestStatus.APPROVED)

    result = await store.get_pending_requests(db)
    assert len(result) == 1
    assert result[0].description == "Delete meeting"


async def test_get_request_by_id(db):
    cid = ConversationId("telegram:123456789")
    request_id = await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"summary": "Lunch"},
    )

    result = await store.get_request(db, request_id)
    assert result is not None
    assert result.id == request_id
    assert result.conversation_id == cid
    assert result.description == "Create lunch"
    assert result.tool_name == "create_event"
    assert result.tool_input == {"summary": "Lunch"}
    assert result.status == RequestStatus.PENDING
    assert result.request_type == RequestType.APPROVAL
    assert result.created_at is not None
    assert result.resolved_at is None


async def test_get_request_resolved(db):
    cid = ConversationId("telegram:123456789")
    request_id = await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"s": "L"},
    )
    await store.resolve_request(db, request_id, RequestStatus.APPROVED)

    result = await store.get_request(db, request_id)
    assert result is not None
    assert result.status == RequestStatus.APPROVED
    assert result.resolved_at is not None


async def test_save_request_response(db):
    cid = ConversationId("telegram:123456789")
    request_id = await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"s": "L"},
    )

    await store.save_request_response(db, request_id, "Lunch event created!")

    result = await store.get_request(db, request_id)
    assert result is not None
    assert result.response_text == "Lunch event created!"


async def test_get_request_response_text_none_by_default(db):
    cid = ConversationId("telegram:123456789")
    request_id = await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"s": "L"},
    )

    result = await store.get_request(db, request_id)
    assert result is not None
    assert result.response_text is None


async def test_complete_request(db):
    cid = ConversationId("telegram:123456789")
    request_id = await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"s": "L"},
    )

    await store.resolve_request(db, request_id, RequestStatus.APPROVED)
    await store.save_request_response(db, request_id, "Lunch event created!")
    await store.complete_request(db, request_id)

    result = await store.get_request(db, request_id)
    assert result is not None
    assert result.status == RequestStatus.COMPLETED
    assert result.response_text == "Lunch event created!"


async def test_get_request_not_found(db):
    result = await store.get_request(db, "nonexistent")
    assert result is None


async def test_create_options_request(db):
    cid = ConversationId("telegram:123456789")
    request_id = await store.create_request(
        db,
        cid,
        RequestType.OPTIONS,
        "Pick a time",
        options=["9am", "10am", "11am"],
    )
    result = await store.get_request(db, request_id)
    assert result is not None
    assert result.request_type == RequestType.OPTIONS
    assert result.options == ["9am", "10am", "11am"]


async def test_create_freeform_request(db):
    cid = ConversationId("telegram:123456789")
    request_id = await store.create_request(
        db,
        cid,
        RequestType.FREEFORM,
        "What time works best?",
    )
    result = await store.get_request(db, request_id)
    assert result is not None
    assert result.request_type == RequestType.FREEFORM
    assert result.options is None
    assert result.tool_name == ""


async def test_audit_log(db):
    await store.log_action(
        db,
        action_type="test_action",
        conversation_id=ConversationId("telegram:123456789"),
        details={"key": "value"},
    )

    async with db.execute("SELECT * FROM audit_log") as cursor:
        rows = await cursor.fetchall()
    assert len(rows) == 1


async def test_get_audit_log(db):
    cid = ConversationId("telegram:123456789")
    await store.log_action(db, action_type="action1", conversation_id=cid, details={"k": "v1"})
    await store.log_action(db, action_type="action2", conversation_id=cid, details={"k": "v2"})
    await store.log_action(
        db, action_type="action3", conversation_id=ConversationId("other"), details={"k": "v3"}
    )

    # Get all
    entries = await store.get_audit_log(db)
    assert len(entries) == 3

    # Filter by conversation_id
    entries = await store.get_audit_log(db, conversation_id=cid)
    assert len(entries) == 2

    # Limit
    entries = await store.get_audit_log(db, limit=1)
    assert len(entries) == 1


# --- Conversation lifecycle tests ---


async def test_conversation_has_status(db):
    cid = ConversationId("telegram:123456789")
    await store.save_conversation(db, cid, "[]")

    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == ConversationStatus.ACTIVE


async def test_update_conversation_status(db):
    cid = ConversationId("telegram:123456789")
    await store.save_conversation(db, cid, "[]")

    await store.update_conversation_status(db, cid, ConversationStatus.AWAITING_OWNER)

    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == ConversationStatus.AWAITING_OWNER

    await store.update_conversation_status(db, cid, ConversationStatus.ACTIVE)

    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == ConversationStatus.ACTIVE


async def test_save_conversation_with_expires_at(db):
    cid = ConversationId("telegram:123456789")
    expires = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    await store.save_conversation(db, cid, "[]", expires_at=expires)

    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["expires_at"] == expires

    # Update without expires_at — should preserve existing value
    await store.save_conversation(db, cid, '[{"role": "user", "content": "hi"}]')

    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["expires_at"] == expires


async def test_get_live_conversations(db):
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    cid1 = ConversationId("telegram:111111111")
    cid2 = ConversationId("telegram:222222222")
    await store.save_conversation(db, cid1, "[]", expires_at=future)
    await store.save_conversation(db, cid2, "[]", expires_at=future)
    await store.update_conversation_status(db, cid2, ConversationStatus.AWAITING_OWNER)

    live = await store.get_live_conversations(db)
    cids = [row["conversation_id"] for row in live]
    assert cid1 in cids
    assert cid2 in cids


async def test_cleanup_expired(db):
    past = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    cid = ConversationId("telegram:123456789")
    await store.save_conversation(db, cid, "[]", expires_at=past)

    # Create a pending request for the expired conversation
    await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create event",
        tool_name="create_event",
        tool_input={"s": "Test"},
    )

    count = await store.cleanup_expired(db)
    assert count == 1

    # Conversation should be gone
    conv = await store.get_conversation(db, cid)
    assert conv is None

    # Pending request should be auto-denied
    pending = await store.get_oldest_pending_request(db)
    assert pending is None


async def test_status_resets_to_active_after_awaiting_owner(db):
    """Verify that a conversation can transition from awaiting_owner back to active."""
    cid = ConversationId("telegram:123456789")
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    await store.save_conversation(db, cid, "[]", expires_at=future)

    # Transition to awaiting_owner
    await store.update_conversation_status(db, cid, ConversationStatus.AWAITING_OWNER)
    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == ConversationStatus.AWAITING_OWNER

    # Reset to active (simulates post-resolution follow-up)
    await store.update_conversation_status(db, cid, ConversationStatus.ACTIVE)
    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == ConversationStatus.ACTIVE

    # Should still appear in live conversations
    live = await store.get_live_conversations(db)
    cids = [row["conversation_id"] for row in live]
    assert cid in cids


async def test_cleanup_skips_unexpired(db):
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    cid = ConversationId("telegram:123456789")
    await store.save_conversation(db, cid, "[]", expires_at=future)

    count = await store.cleanup_expired(db)
    assert count == 0

    conv = await store.get_conversation(db, cid)
    assert conv is not None


# --- Per-conversation pending requests ---


async def test_get_pending_requests_for_conversation(db):
    cid1 = ConversationId("telegram:111111111")
    cid2 = ConversationId("telegram:222222222")

    await store.create_request(
        db,
        cid1,
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"s": "Lunch"},
    )
    await store.create_request(
        db,
        cid1,
        RequestType.APPROVAL,
        "Delete meeting",
        tool_name="delete_event",
        tool_input={"id": "123"},
    )
    await store.create_request(
        db,
        cid2,
        RequestType.APPROVAL,
        "Send email",
        tool_name="send_email",
        tool_input={"to": "bob"},
    )

    result1 = await store.get_pending_requests_for_conversation(db, cid1)
    assert len(result1) == 2
    assert result1[0].description == "Create lunch"
    assert result1[1].description == "Delete meeting"

    result2 = await store.get_pending_requests_for_conversation(db, cid2)
    assert len(result2) == 1
    assert result2[0].description == "Send email"


async def test_get_pending_requests_for_conversation_excludes_resolved(db):
    cid = ConversationId("telegram:111111111")

    id1 = await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create lunch",
        tool_name="create_event",
        tool_input={"s": "Lunch"},
    )
    await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Delete meeting",
        tool_name="delete_event",
        tool_input={"id": "123"},
    )

    await store.resolve_request(db, id1, RequestStatus.APPROVED)

    result = await store.get_pending_requests_for_conversation(db, cid)
    assert len(result) == 1
    assert result[0].description == "Delete meeting"


async def test_get_pending_requests_for_conversation_empty(db):
    cid = ConversationId("telegram:111111111")
    result = await store.get_pending_requests_for_conversation(db, cid)
    assert result == []


# --- delete_conversation ---


async def test_delete_conversation(db):
    cid = ConversationId("telegram:123456789")
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    await store.save_conversation(db, cid, "[]", expires_at=future)
    await store.create_request(
        db,
        cid,
        RequestType.APPROVAL,
        "Create event",
        tool_name="create_event",
        tool_input={"s": "test"},
    )

    deleted = await store.delete_conversation(db, cid)
    assert deleted is True

    # Conversation should be gone
    conv = await store.get_conversation(db, cid)
    assert conv is None

    # Associated requests should be gone
    requests = await store.get_pending_requests_for_conversation(db, cid)
    assert requests == []


async def test_delete_conversation_not_found(db):
    deleted = await store.delete_conversation(db, ConversationId("nonexistent"))
    assert deleted is False
