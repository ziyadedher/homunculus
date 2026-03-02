"""Admin API router — owner-only endpoints for contacts, conversations, requests, and audit log."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from homunculus.channels.router import MessageRouter
from homunculus.server.dependencies import AppState, get_router, get_state, require_owner
from homunculus.storage import store
from homunculus.types import (
    ContactId,
    ConversationId,
    Message,
    RequestId,
    RequestStatus,
)
from homunculus.utils.logging import get_logger
from homunculus.utils.validation import validate_email, validate_phone, validate_timezone

log = get_logger()


# --- Pydantic models ---


class ContactResponse(BaseModel):
    contact_id: str
    name: str
    phone: str | None = None
    email: str | None = None
    timezone: str | None = None
    notes: str | None = None
    telegram_chat_id: str | None = None


class ContactCreateRequest(BaseModel):
    contact_id: str
    name: str
    phone: str | None = None
    email: str | None = None
    timezone: str | None = None
    notes: str | None = None
    telegram_chat_id: str | None = None


class ContactUpdateRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    timezone: str | None = None
    notes: str | None = None
    telegram_chat_id: str | None = None


class ConversationSummary(BaseModel):
    conversation_id: str
    status: str
    updated_at: str
    expires_at: str | None
    message_count: int
    total_requests: int
    request_id: str | None
    request_description: str | None


class MessageItem(BaseModel):
    role: str
    content: str | list[dict[str, object]]
    timestamp: str


class ConversationDetail(BaseModel):
    conversation_id: str
    status: str
    created_at: str
    updated_at: str
    expires_at: str | None
    messages: list[MessageItem]


class OwnerRequestResponse(BaseModel):
    id: str
    conversation_id: str
    request_type: str
    description: str
    tool_name: str
    tool_input: dict[str, object]
    options: list[str] | None
    status: str
    created_at: str
    resolved_at: str | None = None
    response_text: str | None = None


class ResolveRequestBody(BaseModel):
    status: str
    response_text: str | None = None


class AuditLogEntry(BaseModel):
    timestamp: str | None
    action_type: str
    conversation_id: str | None
    details: dict[str, object]


class DeleteResponse(BaseModel):
    deleted: bool


# --- Router ---

admin_router = APIRouter(prefix="/api", tags=["admin"])


# --- Contacts ---


@admin_router.get("/contacts", response_model=list[ContactResponse])
async def list_contacts(
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> list[ContactResponse]:
    contacts = await store.list_contacts(state.db)
    return [
        ContactResponse(
            contact_id=c.contact_id,
            name=c.name,
            phone=c.phone,
            email=c.email,
            timezone=c.timezone,
            notes=c.notes,
            telegram_chat_id=c.telegram_chat_id,
        )
        for c in contacts
    ]


@admin_router.get("/contacts/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: str,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> ContactResponse:
    contact = await store.get_contact(state.db, ContactId(contact_id))
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return ContactResponse(
        contact_id=contact.contact_id,
        name=contact.name,
        phone=contact.phone,
        email=contact.email,
        timezone=contact.timezone,
        notes=contact.notes,
        telegram_chat_id=contact.telegram_chat_id,
    )


@admin_router.post("/contacts", response_model=ContactResponse, status_code=201)
async def create_contact(
    body: ContactCreateRequest,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> ContactResponse:
    # Validate fields server-side
    try:
        if body.phone is not None:
            body.phone = validate_phone(body.phone)
        if body.email is not None:
            body.email = validate_email(body.email)
        if body.timezone is not None:
            body.timezone = validate_timezone(body.timezone)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Check for duplicate
    existing = await store.get_contact(state.db, ContactId(body.contact_id))
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"contact_id '{body.contact_id}' already exists"
        )

    cid = ContactId(body.contact_id)
    await store.create_contact(
        state.db,
        contact_id=cid,
        name=body.name,
        phone=body.phone,
        email=body.email,
        timezone=body.timezone,
        notes=body.notes,
        telegram_chat_id=body.telegram_chat_id,
    )
    contact = await store.get_contact(state.db, cid)
    assert contact is not None
    return ContactResponse(
        contact_id=contact.contact_id,
        name=contact.name,
        phone=contact.phone,
        email=contact.email,
        timezone=contact.timezone,
        notes=contact.notes,
        telegram_chat_id=contact.telegram_chat_id,
    )


@admin_router.patch("/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: str,
    body: ContactUpdateRequest,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> ContactResponse:
    # Validate fields server-side
    try:
        if body.phone is not None:
            body.phone = validate_phone(body.phone)
        if body.email is not None:
            body.email = validate_email(body.email)
        if body.timezone is not None:
            body.timezone = validate_timezone(body.timezone)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields:
        updated = await store.update_contact(state.db, ContactId(contact_id), fields)
        if not updated:
            raise HTTPException(status_code=404, detail="contact not found")

    contact = await store.get_contact(state.db, ContactId(contact_id))
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return ContactResponse(
        contact_id=contact.contact_id,
        name=contact.name,
        phone=contact.phone,
        email=contact.email,
        timezone=contact.timezone,
        notes=contact.notes,
        telegram_chat_id=contact.telegram_chat_id,
    )


@admin_router.delete("/contacts/{contact_id}", response_model=DeleteResponse)
async def delete_contact(
    contact_id: str,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> DeleteResponse:
    deleted = await store.delete_contact(state.db, ContactId(contact_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="contact not found")
    return DeleteResponse(deleted=True)


# --- Conversations ---


@admin_router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> list[ConversationSummary]:
    convs = await store.get_live_conversations(state.db)
    return [
        ConversationSummary(
            conversation_id=str(c["conversation_id"]),
            status=str(c["status"]),
            updated_at=str(c["updated_at"]),
            expires_at=str(c["expires_at"]) if c["expires_at"] else None,
            message_count=c["message_count"] if isinstance(c["message_count"], int) else 0,
            total_requests=c["total_requests"] if isinstance(c["total_requests"], int) else 0,
            request_id=str(c["request_id"]) if c["request_id"] else None,
            request_description=str(c["request_description"]) if c["request_description"] else None,
        )
        for c in convs
    ]


@admin_router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> ConversationDetail:
    conv = await store.get_conversation(state.db, ConversationId(conversation_id))
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    raw_messages = json.loads(str(conv["messages"]))
    messages = []
    for msg_data in raw_messages:
        msg = Message.from_dict(msg_data)
        messages.append(
            MessageItem(
                role=msg.role,
                content=msg.content,
                timestamp=msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )

    return ConversationDetail(
        conversation_id=str(conv["conversation_id"]),
        status=str(conv["status"]),
        created_at=str(conv["created_at"]),
        updated_at=str(conv["updated_at"]),
        expires_at=str(conv["expires_at"]) if conv["expires_at"] else None,
        messages=messages,
    )


@admin_router.delete("/conversations/{conversation_id}", response_model=DeleteResponse)
async def delete_conversation(
    conversation_id: str,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> DeleteResponse:
    deleted = await store.delete_conversation(state.db, ConversationId(conversation_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="conversation not found")
    return DeleteResponse(deleted=True)


# --- Requests ---


@admin_router.get("/requests", response_model=list[OwnerRequestResponse])
async def list_requests(
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> list[OwnerRequestResponse]:
    reqs = await store.get_pending_requests(state.db)
    return [
        OwnerRequestResponse(
            id=r.id,
            conversation_id=r.conversation_id,
            request_type=r.request_type,
            description=r.description,
            tool_name=r.tool_name,
            tool_input=r.tool_input,
            options=r.options,
            status=r.status,
            created_at=r.created_at,
            resolved_at=r.resolved_at,
            response_text=r.response_text,
        )
        for r in reqs
    ]


_VALID_RESOLVE_STATUSES = {"approved", "denied", "resolved"}


@admin_router.post("/requests/{request_id}/resolve", response_model=OwnerRequestResponse)
async def resolve_request(
    request_id: str,
    body: ResolveRequestBody,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
    msg_router: MessageRouter = Depends(get_router),
) -> OwnerRequestResponse:
    if body.status not in _VALID_RESOLVE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of: {', '.join(sorted(_VALID_RESOLVE_STATUSES))}",
        )

    rid = RequestId(request_id)
    req = await store.get_request(state.db, rid)
    if req is None:
        raise HTTPException(status_code=404, detail="request not found")
    if req.status.value != "pending":
        raise HTTPException(status_code=409, detail="request already resolved")

    status = RequestStatus(body.status)
    await store.resolve_request(state.db, rid, status)

    if body.response_text is not None:
        await store.save_request_response(state.db, rid, body.response_text)

    await store.log_action(
        state.db,
        action_type=f"request_{body.status}",
        conversation_id=req.conversation_id,
        details={"request_id": request_id},
    )

    # Trigger conversation resume (mirrors Telegram callback handler)
    updated_req = await store.get_request(state.db, rid)
    if updated_req is not None:
        await msg_router.handle_request_callback(updated_req, body.status)

    # Re-fetch after callback (status may now be "completed")
    final_req = await store.get_request(state.db, rid)
    if final_req is None:
        raise HTTPException(status_code=404, detail="request not found after resolution")
    return OwnerRequestResponse(
        id=final_req.id,
        conversation_id=final_req.conversation_id,
        request_type=final_req.request_type,
        description=final_req.description,
        tool_name=final_req.tool_name,
        tool_input=final_req.tool_input,
        options=final_req.options,
        status=final_req.status,
        created_at=final_req.created_at,
        resolved_at=final_req.resolved_at,
        response_text=final_req.response_text,
    )


# --- Audit Log ---


@admin_router.get("/audit-log", response_model=list[AuditLogEntry])
async def get_audit_log(
    conversation_id: str | None = None,
    limit: int = 50,
    owner_email: str = Depends(require_owner),
    state: AppState = Depends(get_state),
) -> list[AuditLogEntry]:
    cid = ConversationId(conversation_id) if conversation_id else None
    entries = await store.get_audit_log(state.db, conversation_id=cid, limit=limit)
    return [
        AuditLogEntry(
            timestamp=str(e["timestamp"]) if e["timestamp"] else None,
            action_type=str(e["action_type"]),
            conversation_id=str(e["conversation_id"]) if e["conversation_id"] else None,
            details=e["details"] if isinstance(e["details"], dict) else {},
        )
        for e in entries
    ]
