import json
import uuid
from pathlib import Path

import aiosqlite

from homunculus.types import (
    Contact,
    ContactId,
    ConversationId,
    ConversationStatus,
    Message,
    OwnerRequest,
    RequestId,
    RequestStatus,
    RequestType,
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


async def append_message(
    db: aiosqlite.Connection,
    conversation_id: ConversationId,
    message: Message,
) -> None:
    """Append a single message to an existing (or new) conversation's history."""
    raw = await get_conversation_json(db, conversation_id)
    history = json.loads(raw)
    history.append(message.to_dict())
    await save_conversation(db, conversation_id, json.dumps(history))


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
                  (SELECT COUNT(*) FROM owner_requests r2
                   WHERE r2.conversation_id = c.conversation_id) AS total_requests,
                  r.id AS request_id, r.description AS request_description
           FROM conversations c
           LEFT JOIN owner_requests r
             ON r.conversation_id = c.conversation_id AND r.status = 'pending'
           WHERE c.status IN ('active', 'awaiting_owner')
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
            "request_id": row["request_id"],
            "request_description": row["request_description"],
        }
        for row in rows
    ]


async def delete_conversation(db: aiosqlite.Connection, conversation_id: ConversationId) -> bool:
    await db.execute(
        "DELETE FROM owner_requests WHERE conversation_id = ?",
        (conversation_id,),
    )
    cursor = await db.execute(
        "DELETE FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def cleanup_expired(db: aiosqlite.Connection) -> int:
    # Auto-deny pending requests for expired conversations
    await db.execute(
        """UPDATE owner_requests
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


# --- Owner Requests ---


def _row_to_request(row: aiosqlite.Row) -> OwnerRequest:
    options_raw = row["options"]
    options = json.loads(options_raw) if options_raw is not None else None
    return OwnerRequest(
        id=RequestId(row["id"]),
        conversation_id=ConversationId(row["conversation_id"]),
        request_type=RequestType(row["request_type"]),
        description=row["description"],
        tool_name=row["tool_name"],
        tool_input=json.loads(row["tool_input"]),
        options=options,
        status=RequestStatus(row["status"]),
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        response_text=row["response_text"],
    )


_REQUEST_COLS = (
    "id, conversation_id, request_type, description, tool_name, tool_input,"
    " options, status, created_at, resolved_at, response_text"
)


async def create_request(
    db: aiosqlite.Connection,
    conversation_id: ConversationId,
    request_type: RequestType,
    description: str,
    tool_name: str = "",
    tool_input: dict[str, object] | None = None,
    options: list[str] | None = None,
) -> RequestId:
    request_id = RequestId(uuid.uuid4().hex)
    await db.execute(
        """INSERT INTO owner_requests
           (id, conversation_id, request_type, description, tool_name, tool_input, options)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            request_id,
            conversation_id,
            request_type,
            description,
            tool_name,
            json.dumps(tool_input or {}),
            json.dumps(options) if options is not None else None,
        ),
    )
    await db.commit()
    return request_id


async def get_oldest_pending_request(
    db: aiosqlite.Connection,
) -> OwnerRequest | None:
    async with db.execute(
        f"""SELECT {_REQUEST_COLS}
           FROM owner_requests
           WHERE status = 'pending'
           ORDER BY created_at ASC
           LIMIT 1""",
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_request(row)


async def get_pending_requests(
    db: aiosqlite.Connection,
) -> list[OwnerRequest]:
    async with db.execute(
        f"""SELECT {_REQUEST_COLS}
           FROM owner_requests
           WHERE status = 'pending'
           ORDER BY created_at ASC""",
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_request(row) for row in rows]


async def get_pending_requests_for_conversation(
    db: aiosqlite.Connection,
    conversation_id: ConversationId,
) -> list[OwnerRequest]:
    async with db.execute(
        f"""SELECT {_REQUEST_COLS}
           FROM owner_requests
           WHERE status = 'pending' AND conversation_id = ?
           ORDER BY created_at ASC""",
        (conversation_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_request(row) for row in rows]


async def get_request(
    db: aiosqlite.Connection,
    request_id: RequestId,
) -> OwnerRequest | None:
    async with db.execute(
        f"""SELECT {_REQUEST_COLS}
           FROM owner_requests
           WHERE id = ?""",
        (request_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_request(row)


async def resolve_request(
    db: aiosqlite.Connection, request_id: RequestId, status: RequestStatus
) -> None:
    await db.execute(
        """UPDATE owner_requests
           SET status = ?, resolved_at = datetime('now')
           WHERE id = ?""",
        (status, request_id),
    )
    await db.commit()


async def save_request_response(
    db: aiosqlite.Connection, request_id: RequestId, response_text: str
) -> None:
    await db.execute(
        "UPDATE owner_requests SET response_text = ? WHERE id = ?",
        (response_text, request_id),
    )
    await db.commit()


async def complete_request(db: aiosqlite.Connection, request_id: RequestId) -> None:
    """Mark a request as completed (agent has processed it and response is ready)."""
    await db.execute(
        "UPDATE owner_requests SET status = ? WHERE id = ?",
        (RequestStatus.COMPLETED, request_id),
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


async def soft_reset(db: aiosqlite.Connection) -> None:
    """Delete transient data (conversations, requests, audit log). Preserves contacts/creds."""
    await db.execute("DELETE FROM owner_requests")
    await db.execute("DELETE FROM conversations")
    await db.execute("DELETE FROM audit_log")
    await db.commit()


async def hard_reset(db: aiosqlite.Connection) -> None:
    """Delete all data except schema_version. Destroys contacts, credentials, and sessions."""
    await db.execute("DELETE FROM owner_requests")
    await db.execute("DELETE FROM conversations")
    await db.execute("DELETE FROM audit_log")
    await db.execute("DELETE FROM contacts")
    await db.execute("DELETE FROM google_credentials")
    await db.execute("DELETE FROM auth_sessions")
    await db.commit()


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


# --- Auth Sessions ---


async def create_auth_session(
    db: aiosqlite.Connection,
    session_id: str,
    flow_type: str,
    state: str,
    expires_at: str,
    code_verifier: str | None = None,
) -> None:
    await db.execute(
        """INSERT INTO auth_sessions (session_id, flow_type, state, expires_at, code_verifier)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, flow_type, state, expires_at, code_verifier),
    )
    await db.commit()


async def get_auth_session(db: aiosqlite.Connection, session_id: str) -> dict[str, object] | None:
    async with db.execute(
        """SELECT session_id, flow_type, state, email,
                  credentials_json, created_at, expires_at, code_verifier
           FROM auth_sessions WHERE session_id = ?""",
        (session_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "session_id": row["session_id"],
        "flow_type": row["flow_type"],
        "state": row["state"],
        "email": row["email"],
        "credentials_json": row["credentials_json"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "code_verifier": row["code_verifier"],
    }


async def get_auth_session_by_state(
    db: aiosqlite.Connection, state: str
) -> dict[str, object] | None:
    async with db.execute(
        """SELECT session_id, flow_type, state, email,
                  credentials_json, created_at, expires_at, code_verifier
           FROM auth_sessions WHERE state = ?""",
        (state,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "session_id": row["session_id"],
        "flow_type": row["flow_type"],
        "state": row["state"],
        "email": row["email"],
        "credentials_json": row["credentials_json"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "code_verifier": row["code_verifier"],
    }


async def complete_identity_session(
    db: aiosqlite.Connection, session_id: str, email: str, credentials_json: str
) -> None:
    await db.execute(
        "UPDATE auth_sessions SET email = ?, credentials_json = ? WHERE session_id = ?",
        (email, credentials_json, session_id),
    )
    await db.commit()


async def complete_service_session(
    db: aiosqlite.Connection, session_id: str, credentials_json: str
) -> None:
    await db.execute(
        "UPDATE auth_sessions SET credentials_json = ? WHERE session_id = ?",
        (credentials_json, session_id),
    )
    await db.commit()


async def cleanup_expired_sessions(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        "DELETE FROM auth_sessions WHERE expires_at <= datetime('now')",
    )
    count = cursor.rowcount
    await db.commit()
    return count


# --- Google Credentials ---


async def save_google_credentials(
    db: aiosqlite.Connection, email: str, service: str, credentials_json: str, scopes: str
) -> None:
    await db.execute(
        """INSERT INTO google_credentials (email, service, credentials_json, scopes, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(email, service) DO UPDATE SET
             credentials_json = excluded.credentials_json,
             scopes = excluded.scopes,
             updated_at = datetime('now')""",
        (email, service, credentials_json, scopes),
    )
    await db.commit()


async def get_google_credentials(
    db: aiosqlite.Connection, email: str, service: str
) -> dict[str, object] | None:
    async with db.execute(
        """SELECT email, service, credentials_json, scopes, updated_at
           FROM google_credentials WHERE email = ? AND service = ?""",
        (email, service),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "email": row["email"],
        "service": row["service"],
        "credentials_json": row["credentials_json"],
        "scopes": row["scopes"],
        "updated_at": row["updated_at"],
    }
