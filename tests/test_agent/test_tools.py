import json

from homunculus.agent.tools.owner import make_owner_tools
from homunculus.agent.tools.registry import ToolDef, ToolRegistry


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


async def test_owner_tools(db):
    tools = make_owner_tools(db)
    assert len(tools) == 1
    assert tools[0].name == "escalate_to_owner"

    result = await tools[0].handler(
        question="Can I create a lunch event?",
        conversation_id="sms:+11234567890",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "escalated"
    assert "approval_id" in parsed


async def test_owner_tools_no_tool_name_param(db):
    """escalate_to_owner should not require tool_name/tool_input params."""
    tools = make_owner_tools(db)
    # Should work without tool_name and tool_input
    result = await tools[0].handler(
        question="General question for owner",
        conversation_id="sms:+11234567890",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "escalated"
