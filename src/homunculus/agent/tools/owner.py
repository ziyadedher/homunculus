import json

import aiosqlite

from homunculus.agent.tools.registry import ToolDef
from homunculus.storage import store
from homunculus.types import ContactId, ConversationId, RequestId, RequestStatus, RequestType


def make_owner_tools(db: aiosqlite.Connection) -> list[ToolDef]:
    async def ask_owner_question(
        question: str,
        conversation_id: str,
        contact_id: str = "",
        response_type: str = "freeform",
        options: str = "",
    ) -> str:
        cid = ConversationId(conversation_id)
        req_type = RequestType(response_type)
        parsed_options = None
        if req_type == RequestType.OPTIONS and options:
            parsed_options = [o.strip() for o in options.split(",") if o.strip()]
        request_id = await store.create_request(
            db,
            conversation_id=cid,
            request_type=req_type,
            description=question,
            options=parsed_options,
            contact_id=ContactId(contact_id),
        )
        await store.log_action(
            db,
            action_type="owner_request_created",
            conversation_id=cid,
            details={"request_id": request_id, "question": question, "type": response_type},
        )
        return json.dumps(
            {
                "status": "pending",
                "request_id": request_id,
                "message": (
                    "Question sent to owner. The conversation will resume when the owner responds."
                ),
            }
        )

    async def resolve_question(
        request_id: str,
        answer: str,
    ) -> str:
        rid = RequestId(request_id)
        req = await store.get_request(db, rid)
        if req is None:
            return json.dumps({"error": "Request not found"})
        if req.request_type != RequestType.FREEFORM:
            return json.dumps({"error": "Only freeform requests can be resolved this way"})
        if req.status != RequestStatus.PENDING:
            return json.dumps({"error": f"Request is not pending (status: {req.status})"})
        await store.resolve_request(db, rid, RequestStatus.RESOLVED)
        await store.save_request_response(db, rid, answer)
        return json.dumps({"status": "resolved", "request_id": request_id})

    return [
        ToolDef(
            name="ask_owner_question",
            description=(
                "Send a question or message to the owner for their input. "
                "Use this for general questions or when you need the owner's guidance. "
                "Tool approvals for actions like creating events are handled automatically — "
                "you don't need to use this tool for those."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question or message to send to the owner",
                    },
                    "response_type": {
                        "type": "string",
                        "enum": ["approval", "options", "freeform"],
                        "description": (
                            "Type of response expected: 'approval' for yes/no,"
                            " 'options' for pick-from-list, 'freeform' for open-ended"
                        ),
                        "default": "freeform",
                    },
                    "options": {
                        "type": "string",
                        "description": (
                            "Comma-separated list of options (only used when"
                            " response_type is 'options')"
                        ),
                        "default": "",
                    },
                },
                "required": ["question"],
            },
            handler=ask_owner_question,
        ),
        ToolDef(
            name="resolve_question",
            description=(
                "Resolve a pending freeform question from another conversation. "
                "Use this when the owner provides an answer to a pending freeform request."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "The ID of the pending freeform request to resolve",
                    },
                    "answer": {
                        "type": "string",
                        "description": "The owner's answer to the question",
                    },
                },
                "required": ["request_id", "answer"],
            },
            handler=resolve_question,
        ),
    ]
