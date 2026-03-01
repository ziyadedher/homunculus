from homunculus.agent.tools.registry import ToolDef, ToolRegistry


async def test_requires_approval_false_by_default():
    async def noop() -> str:
        return ""

    tool = ToolDef(
        name="test_tool",
        description="Test",
        input_schema={"type": "object"},
        handler=noop,
    )
    assert tool.requires_approval is False


async def test_requires_approval_true():
    async def noop() -> str:
        return ""

    tool = ToolDef(
        name="test_tool",
        description="Test",
        input_schema={"type": "object"},
        handler=noop,
        requires_approval=True,
    )
    assert tool.requires_approval is True


async def test_registry_requires_approval():
    registry = ToolRegistry()

    async def noop() -> str:
        return ""

    registry.register(
        ToolDef(
            name="safe_tool",
            description="Safe",
            input_schema={"type": "object"},
            handler=noop,
        )
    )
    registry.register(
        ToolDef(
            name="dangerous_tool",
            description="Dangerous",
            input_schema={"type": "object"},
            handler=noop,
            requires_approval=True,
        )
    )

    assert registry.requires_approval("safe_tool") is False
    assert registry.requires_approval("dangerous_tool") is True
    assert registry.requires_approval("nonexistent") is False
