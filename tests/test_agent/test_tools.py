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


async def test_owner_tools_ask_owner_question(db):
    tools = make_owner_tools(db)
    assert len(tools) == 2
    assert tools[0].name == "ask_owner_question"
    assert tools[1].name == "resolve_question"

    result = await tools[0].handler(
        question="Can I create a lunch event?",
        conversation_id="telegram:123456789",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "pending"
    assert "request_id" in parsed


async def test_owner_tools_ask_owner_question_freeform(db):
    """ask_owner_question should default to freeform response type."""
    tools = make_owner_tools(db)
    result = await tools[0].handler(
        question="General question for owner",
        conversation_id="telegram:123456789",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "pending"

    # Verify the request was created with freeform type
    req = await store.get_request(db, parsed["request_id"])
    assert req is not None
    assert req.request_type == RequestType.FREEFORM


async def test_owner_tools_resolve_question(db):
    """resolve_question should resolve a pending freeform request."""
    tools = make_owner_tools(db)

    # First create a freeform request
    result = await tools[0].handler(
        question="What time works?",
        conversation_id="telegram:123456789",
        response_type="freeform",
    )
    parsed = json.loads(result)
    request_id = parsed["request_id"]

    # Now resolve it
    result = await tools[1].handler(
        request_id=request_id,
        answer="3pm works best",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "resolved"

    # Verify the request was resolved
    req = await store.get_request(db, request_id)
    assert req is not None
    assert req.response_text == "3pm works best"
