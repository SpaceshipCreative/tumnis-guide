"""The minimal recording mock of the Tumnis MCP server (P2-11, SAF-6).

It serves exactly the tools of the committed catalogue (schemas/mcp/v1/tools.json, which
`make gen` writes from the op registry), with the catalogue's input schemas, so a tool a
later work package adds is on the mock the moment it is in the catalogue. Every call is
answered with a canned result and recorded (server, tool, arguments, result) in memory and,
with `--record`, as one JSON line per call; the hostile suite judges runs from those lines.
It never does anything: no database, no network, and it speaks MCP over stdio only, so it
opens no socket at all. P2-12's full mock server with seed data extends this one.

    python -m harness.mock_mcp_min --record calls.jsonl      # stdio, as Hermes runs it

The response policy is a hook: `respond(tool, arguments) -> result`. The default answers
`request_approval` (once P2-05 puts it in the catalogue) with `pending`, which is what a
tainted run gets, so a gated call after it still fails the judge.

Built on the low-level `mcp.server.Server` of the MCP Python SDK 2.x (on_list_tools and
on_call_tool handlers returning ListToolsResult and CallToolResult), because the tool
schemas come from a JSON file rather than Python signatures.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Final

import anyio

from harness import REPO

# The MCP SDK is imported where a server is built or served, not here: the harness's case
# loading and judging (harness.calls, harness.cases, harness.run) use this module's records
# only, and the traceability check collects the profile tests with the backend's
# environment, whose MCP SDK is an older major version (P2-12).
if TYPE_CHECKING:
    from mcp.server import Server, ServerRequestContext
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        PaginatedRequestParams,
    )

CATALOGUE: Final = REPO / "schemas" / "mcp" / "v1" / "tools.json"
TUMNIS: Final = "tumnis"

Respond = Callable[[str, Mapping[str, Any]], dict[str, Any]]
Check = Callable[[str, Mapping[str, Any]], list[str]]  # what is wrong with a call's arguments


@dataclass(frozen=True)
class RecordedCall:
    """One tool call a mock answered (or one Hermes built-in call read from the stream)."""

    server: str
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    result: Any = None

    def as_json(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "result": self.result,
        }


class Recorder:
    """The calls of one run, in order; with a path, each is also appended to it as a JSON
    line (flushed at once), so a separate process can read them."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.calls: list[RecordedCall] = []
        self._lock = Lock()

    def record(
        self, server: str, tool: str, arguments: Mapping[str, Any], result: Any
    ) -> RecordedCall:
        call = RecordedCall(server, tool, dict(arguments), result)
        with self._lock:
            self.calls.append(call)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as out:
                    out.write(json.dumps(call.as_json(), ensure_ascii=False) + "\n")
        return call


def read_records(path: Path) -> list[RecordedCall]:
    """The calls in a recorder's JSON-lines file ([] when it does not exist)."""
    if not path.is_file():
        return []
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            raw = json.loads(line)
            calls.append(
                RecordedCall(
                    str(raw["server"]),
                    str(raw["tool"]),
                    raw.get("arguments") or {},
                    raw.get("result"),
                )
            )
    return calls


def load_catalogue(path: Path = CATALOGUE) -> list[dict[str, Any]]:
    """The committed tool catalogue: a list of {name, description, input_schema, ...}."""
    tools = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tools, list) or not all(isinstance(t, dict) and "name" in t for t in tools):
        raise ValueError(f"{path.name}: a list of tools with names")
    return tools


def default_response(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """What the mock answers: `pending` for an approval request (every hostile run is
    tainted, and nobody is there to approve), a canned acknowledgement otherwise."""
    if tool == "request_approval":
        return {"status": "pending", "approval_id": "apr-mock-0001", "mock": True}
    return {"ok": True, "mock": True}


def build_server(
    catalogue: Sequence[Mapping[str, Any]],
    recorder: Recorder,
    *,
    name: str = TUMNIS,
    respond: Respond = default_response,
    check: Check | None = None,
) -> Server[Any]:
    """A low-level MCP server listing `catalogue`'s tools and recording every call. No
    output schema is declared, so a client never checks the canned results against one.
    With `check` (P2-12's full mock), a call whose arguments it faults is answered as a
    tool error, and recorded with that error."""
    from mcp.server import Server  # noqa: PLC0415  # see the note at the imports
    from mcp.types import (  # noqa: PLC0415
        CallToolResult,
        ListToolsResult,
        TextContent,
        Tool,
    )

    tools = [
        Tool(
            name=str(tool["name"]),
            description=tool.get("description"),
            input_schema=dict(tool.get("input_schema") or {"type": "object"}),
        )
        for tool in catalogue
    ]
    known = {tool.name for tool in tools}

    async def list_tools(
        ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=tools)

    async def call_tool(
        ctx: ServerRequestContext[Any], params: CallToolRequestParams
    ) -> CallToolResult:
        arguments = dict(params.arguments or {})
        problems = (
            [f"unknown tool {params.name}"]
            if params.name not in known
            else ([] if check is None else check(params.name, arguments))
        )
        if problems:
            result: dict[str, Any] = {"error": "; ".join(problems)}
            recorder.record(name, params.name, arguments, result)
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(result))], is_error=True
            )
        result = respond(params.name, arguments)
        recorder.record(name, params.name, arguments, result)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result))],
            structured_content=result,
        )

    return Server(name, on_list_tools=list_tools, on_call_tool=call_tool)


async def serve_stdio(server: Server[Any]) -> None:
    from mcp.server.stdio import stdio_server  # noqa: PLC0415  # see the note at the imports

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m harness.mock_mcp_min", description=__doc__)
    parser.add_argument("--record", type=Path, help="append every call here as a JSON line")
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    args = parser.parse_args(argv)
    server = build_server(load_catalogue(args.catalogue), Recorder(args.record))
    anyio.run(serve_stdio, server)


if __name__ == "__main__":
    main(sys.argv[1:])
