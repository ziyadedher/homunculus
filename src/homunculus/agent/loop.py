import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite
import anthropic

from homunculus.agent.prompt import build_system_prompt
from homunculus.agent.tools.registry import ToolRegistry
from homunculus.storage import store
from homunculus.types import ApprovalId, Contact, ConversationId, ConversationStatus, Message
from homunculus.utils.config import Config
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


def _api_messages(history: list[Message]) -> list[dict[str, str | list[dict[str, object]]]]:
    """Convert Message objects to the format expected by the Anthropic API."""
    return [m.to_api_param() for m in history]


@dataclass
class AgentResult:
    response_text: str | None
    escalation_message: str | None = None
    escalation_approval_id: ApprovalId | None = None


async def process_message(
    message_body: str,
    conversation_id: ConversationId,
    config: Config,
    db: aiosqlite.Connection,
    registry: ToolRegistry,
    contact: Contact | None = None,
    approved_tools: set[str] | None = None,
) -> AgentResult:
    with tracer.start_as_current_span("agent.process_message") as span:
        span.set_attribute("conversation.id", conversation_id)
        return await _process_message_inner(
            message_body, conversation_id, config, db, registry, contact, approved_tools
        )


async def _process_message_inner(
    message_body: str,
    conversation_id: ConversationId,
    config: Config,
    db: aiosqlite.Connection,
    registry: ToolRegistry,
    contact: Contact | None = None,
    approved_tools: set[str] | None = None,
) -> AgentResult:
    # Load conversation history
    raw = await store.get_conversation_json(db, conversation_id)
    history = _load_history(raw)

    # Trim to recent messages
    if len(history) > MAX_CONVERSATION_MESSAGES:
        history = history[-MAX_CONVERSATION_MESSAGES:]

    # Append the new user message
    history.append(Message.user(message_body))

    system_prompt = build_system_prompt(config.owner, contact=contact)

    client = anthropic.AsyncAnthropic(api_key=config.anthropic.api_key)
    tools = registry.get_schemas()

    escalation_message = None
    escalation_approval_id = None

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

                # Transition status based on whether approvals are still pending
                pending = await store.get_pending_approvals_for_conversation(db, conversation_id)
                if pending:
                    approval_expires = _compute_expires_at(config.conversation.approval_ttl_minutes)
                    await store.update_conversation_status(
                        db,
                        conversation_id,
                        ConversationStatus.AWAITING_APPROVAL,
                        expires_at=approval_expires,
                    )
                else:
                    await store.update_conversation_status(
                        db, conversation_id, ConversationStatus.ACTIVE
                    )

                return AgentResult(
                    response_text=response_text,
                    escalation_message=escalation_message,
                    escalation_approval_id=escalation_approval_id,
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
                        approval_id = await store.create_approval(
                            db,
                            conversation_id,
                            f"Agent wants to call {block.name}",
                            block.name,
                            block.input if isinstance(block.input, dict) else {},
                        )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(
                                    {"status": "awaiting_approval", "approval_id": approval_id}
                                ),
                            }
                        )
                        escalation_message = f"Approval needed: {block.name}"
                        escalation_approval_id = approval_id
                        continue

                    log.info("tool_execute", tool=block.name)

                    with tracer.start_as_current_span("tool.execute") as tool_span:
                        tool_span.set_attribute("tool.name", block.name)
                        result = await registry.execute(block.name, block.input)

                    # Check if this was an escalation
                    if block.name == "escalate_to_owner" and isinstance(result, str):
                        parsed = json.loads(result)
                        if parsed.get("status") == "escalated":
                            tool_input_dict = block.input if isinstance(block.input, dict) else {}
                            escalation_message = str(tool_input_dict.get("question", ""))
                            escalation_approval_id = parsed.get("approval_id")

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
