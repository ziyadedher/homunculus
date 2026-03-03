import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import anthropic
from anthropic.types import MessageParam

from homunculus.agent.prompt import build_system_prompt
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.storage import store
from homunculus.types import (
    Contact,
    ContactId,
    ConversationId,
    ConversationStatus,
    Message,
    OwnerRequest,
    RequestId,
    RequestType,
)
from homunculus.utils.config import ServeConfig
from homunculus.utils.logging import get_logger
from homunculus.utils.tracing import get_tracer

log = get_logger()
tracer = get_tracer(__name__)

MAX_TURNS = 10
MAX_CONVERSATION_MESSAGES = 40  # 20 turns = 40 messages (user + assistant)


def _compute_expires_at(ttl_minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _load_history(raw_json: str) -> list[Message]:
    """Deserialize stored conversation JSON into Message objects."""
    return [Message.from_dict(d) for d in json.loads(raw_json)]


def _save_history(history: list[Message]) -> str:
    """Serialize Message objects to JSON for storage."""
    return json.dumps([m.to_dict() for m in history])


def _api_messages(history: list[Message]) -> list[MessageParam]:
    """Convert Message objects to the format expected by the Anthropic API."""
    return cast(list[MessageParam], [m.to_api_param() for m in history])


@dataclass
class AgentResult:
    response_text: str | None
    request_message: str | None = None
    request_id: RequestId | None = None
    resolved_request_ids: list[RequestId] = field(default_factory=list)


async def process_message(
    message_body: str,
    conversation_id: ConversationId,
    config: ServeConfig,
    db: aiosqlite.Connection,
    registry: ToolRegistry,
    contact: Contact | None = None,
    approved_tools: set[str] | None = None,
    pending_requests: list[OwnerRequest] | None = None,
) -> AgentResult:
    with tracer.start_as_current_span("agent.process_message") as span:
        span.set_attribute("conversation.id", conversation_id)
        return await _process_message_inner(
            message_body,
            conversation_id,
            config,
            db,
            registry,
            contact,
            approved_tools,
            pending_requests,
        )


async def _process_message_inner(
    message_body: str,
    conversation_id: ConversationId,
    config: ServeConfig,
    db: aiosqlite.Connection,
    registry: ToolRegistry,
    contact: Contact | None = None,
    approved_tools: set[str] | None = None,
    pending_requests: list[OwnerRequest] | None = None,
) -> AgentResult:
    # Load conversation history
    raw = await store.get_conversation_json(db, conversation_id)
    history = _load_history(raw)

    # Trim to recent messages
    if len(history) > MAX_CONVERSATION_MESSAGES:
        history = history[-MAX_CONVERSATION_MESSAGES:]

    # Append the new user message
    history.append(Message.user(message_body))

    system_prompt = build_system_prompt(
        config.owner, contact=contact, pending_requests=pending_requests
    )

    client = anthropic.AsyncAnthropic(api_key=config.anthropic.api_key)
    tools = registry.get_schemas()

    request_message = None
    request_id = None
    resolved_request_ids: list[RequestId] = []

    for turn in range(MAX_TURNS):
        with tracer.start_as_current_span("agent.turn") as turn_span:
            turn_span.set_attribute("agent.turn", turn)

            response = await client.messages.create(
                model=config.anthropic.model,
                max_tokens=1024,
                system=system_prompt,
                tools=tools,
                messages=_api_messages(history),
            )

            # Build assistant message content from response
            assistant_content: list[dict[str, object]] = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

            history.append(Message.assistant(assistant_content))

            if response.stop_reason == "end_turn":
                # Extract text response
                text_parts = [b.text for b in response.content if b.type == "text"]
                response_text = "\n".join(text_parts) if text_parts else None

                expires_at = _compute_expires_at(config.conversation.ttl_minutes)
                await store.save_conversation(
                    db, conversation_id, _save_history(history), expires_at=expires_at
                )
                await store.log_action(
                    db,
                    action_type="agent_response",
                    conversation_id=conversation_id,
                    details={"response": response_text},
                )

                # Transition status based on whether requests are still pending
                pending = await store.get_pending_requests_for_conversation(db, conversation_id)
                if pending:
                    request_expires = _compute_expires_at(config.conversation.approval_ttl_minutes)
                    await store.update_conversation_status(
                        db,
                        conversation_id,
                        ConversationStatus.AWAITING_OWNER,
                        expires_at=request_expires,
                    )
                else:
                    await store.update_conversation_status(
                        db, conversation_id, ConversationStatus.ACTIVE
                    )

                return AgentResult(
                    response_text=response_text,
                    request_message=request_message,
                    request_id=request_id,
                    resolved_request_ids=resolved_request_ids,
                )

            if response.stop_reason == "tool_use":
                tool_results: list[dict[str, object]] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    # Check if tool requires approval and hasn't been approved
                    if registry.requires_approval(block.name) and (
                        approved_tools is None or block.name not in approved_tools
                    ):
                        rid = await store.create_request(
                            db,
                            conversation_id,
                            RequestType.APPROVAL,
                            f"Agent wants to call {block.name}",
                            tool_name=block.name,
                            tool_input=(block.input if isinstance(block.input, dict) else {}),
                            contact_id=ContactId(contact.contact_id if contact else ""),
                        )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(
                                    {"status": "awaiting_approval", "request_id": rid}
                                ),
                            }
                        )
                        request_message = f"Approval needed: {block.name}"
                        request_id = rid
                        continue

                    log.info("tool_execute", tool=block.name, input=block.input)

                    # Inject conversation context for owner tools
                    tool_input = block.input if isinstance(block.input, dict) else {}
                    if block.name == "ask_owner_question":
                        tool_input = {
                            **tool_input,
                            "conversation_id": conversation_id,
                            "contact_id": contact.contact_id if contact else "",
                        }

                    with tracer.start_as_current_span("tool.execute") as tool_span:
                        tool_span.set_attribute("tool.name", block.name)
                        result = await registry.execute(block.name, tool_input)

                    # Check if this was an ask_owner_question call
                    if block.name == "ask_owner_question" and isinstance(result, str):
                        parsed = json.loads(result)
                        if parsed.get("status") == "pending":
                            tool_input_dict = block.input if isinstance(block.input, dict) else {}
                            request_message = str(tool_input_dict.get("question", ""))
                            request_id = parsed.get("request_id")

                    # Check if this was a resolve_question call
                    if block.name == "resolve_question" and isinstance(result, str):
                        parsed = json.loads(result)
                        if parsed.get("status") == "resolved":
                            resolved_rid = parsed.get("request_id")
                            if resolved_rid:
                                resolved_request_ids.append(RequestId(resolved_rid))

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": (result if isinstance(result, str) else json.dumps(result)),
                        }
                    )

                    await store.log_action(
                        db,
                        action_type="tool_call",
                        conversation_id=conversation_id,
                        details={
                            "tool": block.name,
                            "input": block.input,
                            "result": result,
                        },
                    )

                history.append(Message.user(tool_results))

    # Hit max turns
    expires_at = _compute_expires_at(config.conversation.ttl_minutes)
    await store.save_conversation(
        db, conversation_id, _save_history(history), expires_at=expires_at
    )
    return AgentResult(
        response_text="I'm having trouble processing this request. Let me check with the owner."
    )
