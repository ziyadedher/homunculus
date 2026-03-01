import json
from datetime import UTC, datetime, timedelta

from homunculus.storage import store
from homunculus.types import ConversationId, ConversationStatus


async def test_conversation_roundtrip(db):
    cid = ConversationId("sms:+11234567890")
    messages = '[{"role": "user", "content": "hello"}]'

    await store.save_conversation(db, cid, messages)
    loaded = await store.get_conversation_json(db, cid)
    assert loaded == messages


async def test_conversation_empty(db):
    loaded = await store.get_conversation_json(db, ConversationId("nonexistent"))
    assert loaded == "[]"


async def test_conversation_update(db):
    cid = ConversationId("sms:+11234567890")
    await store.save_conversation(db, cid, '[{"role": "user", "content": "first"}]')
    await store.save_conversation(
        db,
        cid,
        '[{"role": "user", "content": "first"}, {"role": "assistant", "content": "second"}]',
    )

    loaded = json.loads(await store.get_conversation_json(db, cid))
    assert len(loaded) == 2


async def test_approval_lifecycle(db):
    cid = ConversationId("sms:+11234567890")
    approval_id = await store.create_approval(
        db,
        conversation_id=cid,
        request_description="Create lunch event",
        tool_name="create_event",
        tool_input={"summary": "Lunch"},
    )
    assert approval_id

    pending = await store.get_oldest_pending_approval(db)
    assert pending is not None
    assert pending["id"] == approval_id
    assert pending["status"] == "pending"

    await store.resolve_approval(db, approval_id, "approved")

    pending = await store.get_oldest_pending_approval(db)
    assert pending is None


async def test_get_pending_approvals_empty(db):
    result = await store.get_pending_approvals(db)
    assert result == []


async def test_get_pending_approvals_multiple(db):
    cid = ConversationId("sms:+11234567890")
    id1 = await store.create_approval(db, cid, "Create lunch", "create_event", {"s": "Lunch"})
    id2 = await store.create_approval(db, cid, "Delete meeting", "delete_event", {"id": "123"})

    result = await store.get_pending_approvals(db)
    assert len(result) == 2
    assert result[0]["id"] == id1
    assert result[1]["id"] == id2


async def test_get_pending_approvals_excludes_resolved(db):
    cid = ConversationId("sms:+11234567890")
    id1 = await store.create_approval(db, cid, "Create lunch", "create_event", {"s": "Lunch"})
    await store.create_approval(db, cid, "Delete meeting", "delete_event", {"id": "123"})

    await store.resolve_approval(db, id1, "approved")

    result = await store.get_pending_approvals(db)
    assert len(result) == 1
    assert result[0]["request_description"] == "Delete meeting"


async def test_get_approval_by_id(db):
    cid = ConversationId("sms:+11234567890")
    approval_id = await store.create_approval(
        db, cid, "Create lunch", "create_event", {"summary": "Lunch"}
    )

    result = await store.get_approval(db, approval_id)
    assert result is not None
    assert result["id"] == approval_id
    assert result["conversation_id"] == cid
    assert result["request_description"] == "Create lunch"
    assert result["tool_name"] == "create_event"
    assert result["tool_input"] == {"summary": "Lunch"}
    assert result["status"] == "pending"
    assert result["created_at"] is not None
    assert result["resolved_at"] is None


async def test_get_approval_resolved(db):
    cid = ConversationId("sms:+11234567890")
    approval_id = await store.create_approval(db, cid, "Create lunch", "create_event", {"s": "L"})
    await store.resolve_approval(db, approval_id, "approved")

    result = await store.get_approval(db, approval_id)
    assert result is not None
    assert result["status"] == "approved"
    assert result["resolved_at"] is not None


async def test_get_approval_not_found(db):
    result = await store.get_approval(db, "nonexistent")
    assert result is None


async def test_audit_log(db):
    await store.log_action(
        db,
        action_type="test_action",
        conversation_id=ConversationId("sms:+11234567890"),
        details={"key": "value"},
    )

    async with db.execute("SELECT * FROM audit_log") as cursor:
        rows = await cursor.fetchall()
    assert len(rows) == 1


async def test_get_audit_log(db):
    cid = ConversationId("sms:+11234567890")
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
    cid = ConversationId("sms:+11234567890")
    await store.save_conversation(db, cid, "[]")

    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == "active"


async def test_update_conversation_status(db):
    cid = ConversationId("sms:+11234567890")
    await store.save_conversation(db, cid, "[]")

    await store.update_conversation_status(db, cid, ConversationStatus.AWAITING_APPROVAL)

    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == "awaiting_approval"

    await store.update_conversation_status(db, cid, ConversationStatus.ACTIVE)

    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == "active"


async def test_save_conversation_with_expires_at(db):
    cid = ConversationId("sms:+11234567890")
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

    cid1 = ConversationId("sms:+11111111111")
    cid2 = ConversationId("sms:+12222222222")
    await store.save_conversation(db, cid1, "[]", expires_at=future)
    await store.save_conversation(db, cid2, "[]", expires_at=future)
    await store.update_conversation_status(db, cid2, ConversationStatus.AWAITING_APPROVAL)

    live = await store.get_live_conversations(db)
    cids = [row["conversation_id"] for row in live]
    assert cid1 in cids
    assert cid2 in cids


async def test_cleanup_expired(db):
    past = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    cid = ConversationId("sms:+11234567890")
    await store.save_conversation(db, cid, "[]", expires_at=past)

    # Create a pending approval for the expired conversation
    await store.create_approval(db, cid, "Create event", "create_event", {"s": "Test"})

    count = await store.cleanup_expired(db)
    assert count == 1

    # Conversation should be gone
    conv = await store.get_conversation(db, cid)
    assert conv is None

    # Pending approval should be auto-denied
    pending = await store.get_oldest_pending_approval(db)
    assert pending is None


async def test_status_resets_to_active_after_awaiting_approval(db):
    """Verify that a conversation can transition from awaiting_approval back to active."""
    cid = ConversationId("sms:+11234567890")
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    await store.save_conversation(db, cid, "[]", expires_at=future)

    # Transition to awaiting_approval
    await store.update_conversation_status(db, cid, ConversationStatus.AWAITING_APPROVAL)
    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == "awaiting_approval"

    # Reset to active (simulates post-approval follow-up)
    await store.update_conversation_status(db, cid, ConversationStatus.ACTIVE)
    conv = await store.get_conversation(db, cid)
    assert conv is not None
    assert conv["status"] == "active"

    # Should still appear in live conversations
    live = await store.get_live_conversations(db)
    cids = [row["conversation_id"] for row in live]
    assert cid in cids


async def test_cleanup_skips_unexpired(db):
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    cid = ConversationId("sms:+11234567890")
    await store.save_conversation(db, cid, "[]", expires_at=future)

    count = await store.cleanup_expired(db)
    assert count == 0

    conv = await store.get_conversation(db, cid)
    assert conv is not None


# --- Per-conversation pending approvals ---


async def test_get_pending_approvals_for_conversation(db):
    cid1 = ConversationId("sms:+11111111111")
    cid2 = ConversationId("sms:+12222222222")

    await store.create_approval(db, cid1, "Create lunch", "create_event", {"s": "Lunch"})
    await store.create_approval(db, cid1, "Delete meeting", "delete_event", {"id": "123"})
    await store.create_approval(db, cid2, "Send email", "send_email", {"to": "bob"})

    result1 = await store.get_pending_approvals_for_conversation(db, cid1)
    assert len(result1) == 2
    assert result1[0]["request_description"] == "Create lunch"
    assert result1[1]["request_description"] == "Delete meeting"

    result2 = await store.get_pending_approvals_for_conversation(db, cid2)
    assert len(result2) == 1
    assert result2[0]["request_description"] == "Send email"


async def test_get_pending_approvals_for_conversation_excludes_resolved(db):
    cid = ConversationId("sms:+11111111111")

    id1 = await store.create_approval(db, cid, "Create lunch", "create_event", {"s": "Lunch"})
    await store.create_approval(db, cid, "Delete meeting", "delete_event", {"id": "123"})

    await store.resolve_approval(db, id1, "approved")

    result = await store.get_pending_approvals_for_conversation(db, cid)
    assert len(result) == 1
    assert result[0]["request_description"] == "Delete meeting"


async def test_get_pending_approvals_for_conversation_empty(db):
    cid = ConversationId("sms:+11111111111")
    result = await store.get_pending_approvals_for_conversation(db, cid)
    assert result == []


# --- delete_conversation ---


async def test_delete_conversation(db):
    cid = ConversationId("sms:+11234567890")
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    await store.save_conversation(db, cid, "[]", expires_at=future)
    await store.create_approval(db, cid, "Create event", "create_event", {"s": "test"})

    deleted = await store.delete_conversation(db, cid)
    assert deleted is True

    # Conversation should be gone
    conv = await store.get_conversation(db, cid)
    assert conv is None

    # Associated approvals should be gone
    approvals = await store.get_pending_approvals_for_conversation(db, cid)
    assert approvals == []


async def test_delete_conversation_not_found(db):
    deleted = await store.delete_conversation(db, ConversationId("nonexistent"))
    assert deleted is False
