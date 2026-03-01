import json
import uuid
from pathlib import Path

import aiosqlite

from homunculus.types import (
    Approval,
    ApprovalId,
    ApprovalStatus,
    Contact,
    ContactId,
    ConversationId,
    ConversationStatus,
)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def open_store(db_path: Path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await _run_migrations(db)
    return db


async def _run_migrations(db: aiosqlite.Connection) -> None:
    await db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    async with db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version") as cursor:
        row = await cursor.fetchone()
    current_version = row[0] if row else 0

    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        # Extract version number from filename (e.g. "001_initial.sql" -> 1)
        version = int(sql_file.stem.split("_", 1)[0])
        if version <= current_version:
            continue
        await db.executescript(sql_file.read_text())
        await db.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        await db.commit()


# --- Conversations ---


async def get_conversation_json(db: aiosqlite.Connection, conversation_id: ConversationId) -> str:
    async with db.execute(
        "SELECT messages FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return "[]"
    return row["messages"]


async def save_conversation(
    db: aiosqlite.Connection,
    conversation_id: ConversationId,
    messages_json: str,
    expires_at: str | None = None,
) -> None:
    await db.execute(
        """INSERT INTO conversations (conversation_id, messages, updated_at, expires_at)
           VALUES (?, ?, datetime('now'), ?)
           ON CONFLICT(conversation_id) DO UPDATE SET
             messages = excluded.messages,
             updated_at = datetime('now'),
             expires_at = COALESCE(excluded.expires_at, conversations.expires_at)""",
        (conversation_id, messages_json, expires_at),
    )
    await db.commit()


async def get_conversation(
    db: aiosqlite.Connection, conversation_id: ConversationId
) -> dict[str, object] | None:
    async with db.execute(
        """SELECT conversation_id, messages, status, created_at, updated_at, expires_at
           FROM conversations WHERE conversation_id = ?""",
        (conversation_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "conversation_id": row["conversation_id"],
        "messages": row["messages"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
    }


async def update_conversation_status(
    db: aiosqlite.Connection,
    conversation_id: ConversationId,
    status: ConversationStatus,
    expires_at: str | None = None,
) -> None:
    if expires_at is not None:
        await db.execute(
            """UPDATE conversations
               SET status = ?, expires_at = ?, updated_at = datetime('now')
               WHERE conversation_id = ?""",
            (status, expires_at, conversation_id),
        )
    else:
        await db.execute(
            """UPDATE conversations
               SET status = ?, updated_at = datetime('now')
               WHERE conversation_id = ?""",
            (status, conversation_id),
        )
    await db.commit()


async def get_live_conversations(db: aiosqlite.Connection) -> list[dict[str, object]]:
    async with db.execute(
        """SELECT c.conversation_id, c.status, c.updated_at, c.expires_at,
                  COALESCE(json_array_length(c.messages), 0) AS message_count,
                  (SELECT COUNT(*) FROM pending_approvals pa2
                   WHERE pa2.conversation_id = c.conversation_id) AS total_requests,
                  pa.id AS approval_id, pa.request_description
           FROM conversations c
           LEFT JOIN pending_approvals pa
             ON pa.conversation_id = c.conversation_id AND pa.status = 'pending'
           WHERE c.status IN ('active', 'awaiting_approval')
             AND (c.expires_at > datetime('now') OR c.expires_at IS NULL)
           ORDER BY c.updated_at DESC""",
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "conversation_id": row["conversation_id"],
            "status": row["status"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
            "message_count": row["message_count"],
            "total_requests": row["total_requests"],
            "approval_id": row["approval_id"],
            "request_description": row["request_description"],
        }
        for row in rows
    ]


async def delete_conversation(db: aiosqlite.Connection, conversation_id: ConversationId) -> bool:
    await db.execute(
        "DELETE FROM pending_approvals WHERE conversation_id = ?",
        (conversation_id,),
    )
    cursor = await db.execute(
        "DELETE FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def cleanup_expired(db: aiosqlite.Connection) -> int:
    # Auto-deny pending approvals for expired conversations
    await db.execute(
        """UPDATE pending_approvals
           SET status = 'denied', resolved_at = datetime('now')
           WHERE status = 'pending'
             AND conversation_id IN (
               SELECT conversation_id FROM conversations
               WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')
             )""",
    )
    # Delete expired conversation rows
    cursor = await db.execute(
        """DELETE FROM conversations
           WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')""",
    )
    count = cursor.rowcount
    await db.commit()
    return count


# --- Pending Approvals ---


def _row_to_approval(row: aiosqlite.Row) -> Approval:
    return Approval(
        id=ApprovalId(row["id"]),
        conversation_id=ConversationId(row["conversation_id"]),
        request_description=row["request_description"],
        tool_name=row["tool_name"],
        tool_input=json.loads(row["tool_input"]),
        status=ApprovalStatus(row["status"]),
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        response_text=row["response_text"],
    )


async def create_approval(
    db: aiosqlite.Connection,
    conversation_id: ConversationId,
    request_description: str,
    tool_name: str,
    tool_input: dict[str, object],
) -> ApprovalId:
    approval_id = ApprovalId(uuid.uuid4().hex)
    await db.execute(
        """INSERT INTO pending_approvals (id, conversation_id, request_description, tool_name, tool_input)
           VALUES (?, ?, ?, ?, ?)""",
        (approval_id, conversation_id, request_description, tool_name, json.dumps(tool_input)),
    )
    await db.commit()
    return approval_id


async def get_oldest_pending_approval(
    db: aiosqlite.Connection,
) -> Approval | None:
    async with db.execute(
        """SELECT id, conversation_id, request_description, tool_name, tool_input,
                  status, created_at, resolved_at, response_text
           FROM pending_approvals
           WHERE status = 'pending'
           ORDER BY created_at ASC
           LIMIT 1""",
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_approval(row)


async def get_pending_approvals(
    db: aiosqlite.Connection,
) -> list[Approval]:
    async with db.execute(
        """SELECT id, conversation_id, request_description, tool_name, tool_input,
                  status, created_at, resolved_at, response_text
           FROM pending_approvals
           WHERE status = 'pending'
           ORDER BY created_at ASC""",
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_approval(row) for row in rows]


async def get_pending_approvals_for_conversation(
    db: aiosqlite.Connection,
    conversation_id: ConversationId,
) -> list[Approval]:
    async with db.execute(
        """SELECT id, conversation_id, request_description, tool_name, tool_input,
                  status, created_at, resolved_at, response_text
           FROM pending_approvals
           WHERE status = 'pending' AND conversation_id = ?
           ORDER BY created_at ASC""",
        (conversation_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_approval(row) for row in rows]


async def get_approval(
    db: aiosqlite.Connection,
    approval_id: ApprovalId,
) -> Approval | None:
    async with db.execute(
        """SELECT id, conversation_id, request_description, tool_name, tool_input,
                  status, created_at, resolved_at, response_text
           FROM pending_approvals
           WHERE id = ?""",
        (approval_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_approval(row)


async def resolve_approval(
    db: aiosqlite.Connection, approval_id: ApprovalId, status: ApprovalStatus
) -> None:
    await db.execute(
        """UPDATE pending_approvals
           SET status = ?, resolved_at = datetime('now')
           WHERE id = ?""",
        (status, approval_id),
    )
    await db.commit()


async def save_approval_response(
    db: aiosqlite.Connection, approval_id: ApprovalId, response_text: str
) -> None:
    await db.execute(
        "UPDATE pending_approvals SET response_text = ? WHERE id = ?",
        (response_text, approval_id),
    )
    await db.commit()


async def complete_approval(db: aiosqlite.Connection, approval_id: ApprovalId) -> None:
    """Mark an approval as completed (agent has processed it and response is ready)."""
    await db.execute(
        "UPDATE pending_approvals SET status = ? WHERE id = ?",
        (ApprovalStatus.COMPLETED, approval_id),
    )
    await db.commit()


# --- Audit Log ---


async def log_action(
    db: aiosqlite.Connection,
    action_type: str,
    conversation_id: ConversationId | None = None,
    details: dict[str, object] | None = None,
) -> None:
    await db.execute(
        """INSERT INTO audit_log (action_type, conversation_id, details)
           VALUES (?, ?, ?)""",
        (action_type, conversation_id, json.dumps(details or {})),
    )
    await db.commit()


async def get_audit_log(
    db: aiosqlite.Connection,
    conversation_id: ConversationId | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    if conversation_id is not None:
        query = """SELECT timestamp, action_type, conversation_id, details
                   FROM audit_log WHERE conversation_id = ?
                   ORDER BY timestamp DESC LIMIT ?"""
        params: tuple[object, ...] = (conversation_id, limit)
    else:
        query = """SELECT timestamp, action_type, conversation_id, details
                   FROM audit_log ORDER BY timestamp DESC LIMIT ?"""
        params = (limit,)
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "timestamp": row["timestamp"],
            "action_type": row["action_type"],
            "conversation_id": row["conversation_id"],
            "details": json.loads(row["details"]) if row["details"] else {},
        }
        for row in rows
    ]


# --- Contacts ---


def _row_to_contact(row: aiosqlite.Row) -> Contact:
    return Contact(
        contact_id=ContactId(row["contact_id"]),
        name=row["name"],
        phone=row["phone"],
        email=row["email"],
        timezone=row["timezone"],
        notes=row["notes"],
        telegram_chat_id=row["telegram_chat_id"],
    )


async def create_contact(
    db: aiosqlite.Connection,
    contact_id: ContactId,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    timezone: str | None = None,
    notes: str | None = None,
    telegram_chat_id: str | None = None,
) -> ContactId:
    await db.execute(
        """INSERT INTO contacts (contact_id, name, phone, email, timezone, notes, telegram_chat_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (contact_id, name, phone, email, timezone, notes, telegram_chat_id),
    )
    await db.commit()
    return contact_id


async def get_contact(
    db: aiosqlite.Connection,
    contact_id: ContactId,
) -> Contact | None:
    async with db.execute(
        """SELECT contact_id, name, phone, email, timezone, notes, telegram_chat_id
           FROM contacts WHERE contact_id = ?""",
        (contact_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_contact(row)


async def get_contact_by_phone(
    db: aiosqlite.Connection,
    phone: str,
) -> Contact | None:
    async with db.execute(
        """SELECT contact_id, name, phone, email, timezone, notes, telegram_chat_id
           FROM contacts WHERE phone = ?""",
        (phone,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_contact(row)


async def get_contact_by_email(
    db: aiosqlite.Connection,
    email: str,
) -> Contact | None:
    async with db.execute(
        """SELECT contact_id, name, phone, email, timezone, notes, telegram_chat_id
           FROM contacts WHERE email = ?""",
        (email,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_contact(row)


async def get_contact_by_telegram_chat_id(
    db: aiosqlite.Connection,
    telegram_chat_id: str,
) -> Contact | None:
    async with db.execute(
        """SELECT contact_id, name, phone, email, timezone, notes, telegram_chat_id
           FROM contacts WHERE telegram_chat_id = ?""",
        (telegram_chat_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_contact(row)


async def list_contacts(db: aiosqlite.Connection) -> list[Contact]:
    async with db.execute(
        """SELECT contact_id, name, phone, email, timezone, notes, telegram_chat_id
           FROM contacts ORDER BY name"""
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_contact(row) for row in rows]


_CONTACT_UPDATABLE_FIELDS = frozenset(
    {"name", "phone", "email", "timezone", "notes", "telegram_chat_id"}
)


async def update_contact(
    db: aiosqlite.Connection,
    contact_id: ContactId,
    fields: dict[str, str | None],
) -> bool:
    valid = {k: v for k, v in fields.items() if k in _CONTACT_UPDATABLE_FIELDS}
    if not valid:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in valid)
    values = [*valid.values(), contact_id]
    cursor = await db.execute(
        f"UPDATE contacts SET {set_clause} WHERE contact_id = ?",
        values,
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_contact(
    db: aiosqlite.Connection,
    contact_id: ContactId,
) -> bool:
    cursor = await db.execute(
        "DELETE FROM contacts WHERE contact_id = ?",
        (contact_id,),
    )
    await db.commit()
    return cursor.rowcount > 0
