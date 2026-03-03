from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from anthropic.types import ToolParam

from homunculus.utils.logging import get_logger

log = get_logger()


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, object]
    handler: Callable[..., Awaitable[str]]
    requires_approval: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def get_schemas(self) -> list[ToolParam]:
        return [
            ToolParam(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
            )
            for t in self._tools.values()
        ]

    def requires_approval(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool.requires_approval if tool is not None else False

    async def execute(self, name: str, tool_input: dict[str, object]) -> str | dict[str, str]:
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await tool.handler(**tool_input)
        except Exception as e:
            log.warning("tool_failed", tool=name, error=str(e))
            return {"error": str(e)}
