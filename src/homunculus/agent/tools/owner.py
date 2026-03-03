import json

import aiosqlite

from homunculus.agent.tools.registry import ToolDef
from homunculus.storage import store
from homunculus.types import (
    ChannelId,
    ContactId,
    ConversationId,
    RequestId,
    RequestStatus,
    RequestType,
)


def make_owner_tools(db: aiosqlite.Connection) -> list[ToolDef]:
    async def send_message(
        message: str,
        context: str = "",
        conversation_id: str = "",
        contact_id: str = "",
        channel_id: str = "",
    ) -> str:
        cid = ConversationId(conversation_id)
        ch_id = ChannelId(channel_id) if channel_id else None
        request_id = await store.create_request(
            db,
            conversation_id=cid,
            request_type=RequestType.FREEFORM,
            description=message,
            contact_id=ContactId(contact_id),
            channel_id=ch_id,
            context=context,
        )
        await store.log_action(
            db,
            action_type="message_sent_to_owner",
            conversation_id=cid,
            details={"request_id": request_id, "message": message, "context": context},
        )
        return json.dumps(
            {
                "status": "pending",
                "request_id": request_id,
                "message": (
                    "Message sent to the owner's agent. "
                    "The conversation will resume when they respond."
                ),
            }
        )

    async def reply_to_message(
        message_id: str,
        response: str,
    ) -> str:
        rid = RequestId(message_id)
        req = await store.get_request(db, rid)
        if req is None:
            return json.dumps({"error": "Message not found"})
        if req.status != RequestStatus.PENDING:
            return json.dumps({"error": f"Message is not pending (status: {req.status})"})
        await store.resolve_request(db, rid, RequestStatus.RESOLVED)
        await store.save_request_response(db, rid, response)
        return json.dumps({"status": "resolved", "request_id": message_id})

    return [
        ToolDef(
            name="send_message",
            description=(
                "Send a message to the owner's agent for their input. "
                "The owner's agent will present it to the owner and respond when ready. "
                "Use this when you need the owner's guidance or decision. "
                "Tool approvals for actions like creating events are handled automatically — "
                "you don't need to use this tool for those."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message or question to send",
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "Background context for the receiving agent: "
                            "who is asking, why, and any relevant conversation history"
                        ),
                        "default": "",
                    },
                },
                "required": ["message"],
            },
            handler=send_message,
        ),
        ToolDef(
            name="reply_to_message",
            description=(
                "Reply to a pending message from another conversation's agent. "
                "Use this when the owner provides an answer to a pending request."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The ID of the pending message to reply to",
                    },
                    "response": {
                        "type": "string",
                        "description": "The response to send back",
                    },
                },
                "required": ["message_id", "response"],
            },
            handler=reply_to_message,
        ),
    ]
