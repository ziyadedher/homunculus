import json

import aiosqlite

from homunculus.agent.tools.registry import ToolDef
from homunculus.storage import store
from homunculus.types import ConversationId


def make_owner_tools(db: aiosqlite.Connection) -> list[ToolDef]:
    async def escalate_to_owner(
        question: str,
        conversation_id: str,
    ) -> str:
        cid = ConversationId(conversation_id)
        approval_id = await store.create_approval(
            db,
            conversation_id=cid,
            request_description=question,
            tool_name="",
            tool_input={},
        )
        await store.log_action(
            db,
            action_type="escalation_created",
            conversation_id=cid,
            details={"approval_id": approval_id, "question": question},
        )
        return json.dumps(
            {
                "status": "escalated",
                "approval_id": approval_id,
                "message": "Question sent to owner. The conversation will resume when the owner responds.",
            }
        )

    return [
        ToolDef(
            name="escalate_to_owner",
            description=(
                "Send a question or message to the owner for their input. "
                "Use this for general questions or when you need the owner's guidance. "
                "Tool approvals for actions like creating events are handled automatically — "
                "you don't need to escalate for those."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question or message to send to the owner",
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": "The conversation ID this escalation belongs to",
                    },
                },
                "required": ["question", "conversation_id"],
            },
            handler=escalate_to_owner,
        ),
    ]
