import json

from homunculus.agent.tools.owner import make_owner_tools
from homunculus.agent.tools.registry import ToolDef, ToolRegistry
from homunculus.storage import store
from homunculus.types import RequestType


async def test_registry_execute():
    registry = ToolRegistry()

    async def echo(text: str) -> str:
        return text

    registry.register(
        ToolDef(
            name="echo",
            description="Echo text",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=echo,
        )
    )

    result = await registry.execute("echo", {"text": "hello"})
    assert result == "hello"


async def test_registry_unknown_tool():
    registry = ToolRegistry()
    result = await registry.execute("nonexistent", {})
    assert "error" in result


async def test_registry_schemas():
    registry = ToolRegistry()

    async def noop() -> str:
        return ""

    registry.register(
        ToolDef(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object"},
            handler=noop,
        )
    )

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "test_tool"


async def test_owner_tools_send_message(db):
    tools = make_owner_tools(db)
    assert len(tools) == 2
    assert tools[0].name == "send_message"
    assert tools[1].name == "reply_to_message"

    result = await tools[0].handler(
        message="Can I create a lunch event?",
        conversation_id="telegram:123456789",
        contact_id="123456789",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "pending"
    assert "request_id" in parsed


async def test_owner_tools_send_message_with_context(db):
    """send_message should create a freeform request with context."""
    tools = make_owner_tools(db)
    result = await tools[0].handler(
        message="What time works for a meeting?",
        context="Alice is asking about scheduling a 1:1 next week",
        conversation_id="telegram:123456789",
        contact_id="123456789",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "pending"

    # Verify the request was created with freeform type and context
    req = await store.get_request(db, parsed["request_id"])
    assert req is not None
    assert req.request_type == RequestType.FREEFORM
    assert req.context == "Alice is asking about scheduling a 1:1 next week"


async def test_owner_tools_reply_to_message(db):
    """reply_to_message should resolve a pending request."""
    tools = make_owner_tools(db)

    # First create a request via send_message
    result = await tools[0].handler(
        message="What time works?",
        conversation_id="telegram:123456789",
        contact_id="123456789",
    )
    parsed = json.loads(result)
    request_id = parsed["request_id"]

    # Now resolve it
    result = await tools[1].handler(
        message_id=request_id,
        response="3pm works best",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "resolved"

    # Verify the request was resolved
    req = await store.get_request(db, request_id)
    assert req is not None
    assert req.response_text == "3pm works best"
