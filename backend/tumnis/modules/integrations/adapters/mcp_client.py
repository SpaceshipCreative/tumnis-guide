"""`McpSource`: the MCP client wrapper (P3-02). Spec stub."""

from collections.abc import Mapping
from typing import Any

TOOL_ALLOWLISTS: Mapping[str, frozenset[str]] = {}


class ToolNotAllowed(Exception):  # noqa: N818  # the plan's name
    def __init__(self, tool: str) -> None:
        super().__init__(tool)
        self.tool = tool


class McpSource:
    def __init__(self, **_: Any) -> None:
        pass

    async def call(self, tool: str, args: Mapping[str, Any]) -> Any:
        raise NotImplementedError
