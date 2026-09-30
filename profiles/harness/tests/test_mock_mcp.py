"""The recording mock MCP servers (P2-11, SAF-6): the Tumnis mock is built from the tool
catalogue (schemas/mcp/v1/tools.json), and every call it answers is recorded with its
arguments. Tests talk to the servers in memory (the SDK's Client), so no socket opens."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest


@pytest.mark.req("SAF-6")
@pytest.mark.wp("P2-11")
@pytest.mark.xfail(strict=True, reason="spec:P2-11")
def test_mock_built_from_catalogue_records_calls(tmp_path: Path) -> None:
    """T-P2-11-07
    Every tool in schemas/mcp/v1/tools.json is listed by the mock with the catalogue's
    input schema; calling each one answers without error, and the recorder holds one call
    per tool, in order, with server `tumnis`, the tool's name and its arguments exactly,
    and writes the same calls to its JSON-lines file.
    """
    from mcp.client import Client

    from harness import REPO
    from harness.mock_mcp_min import Recorder, build_server, load_catalogue

    catalogue = load_catalogue()
    on_disk = json.loads((REPO / "schemas/mcp/v1/tools.json").read_text(encoding="utf-8"))
    assert [tool["name"] for tool in catalogue] == [tool["name"] for tool in on_disk]
    record = tmp_path / "calls.jsonl"
    recorder = Recorder(record)
    server = build_server(catalogue, recorder)
    sent: list[tuple[str, dict[str, Any]]] = [
        (tool["name"], {"probe": n, "note": "zero\u200bwidth"}) for n, tool in enumerate(catalogue)
    ]

    async def exercise() -> None:
        async with Client(server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            by_name = {tool.name: tool for tool in listed.tools}
            assert set(by_name) == {tool["name"] for tool in catalogue}
            for tool in catalogue:
                assert by_name[tool["name"]].input_schema == tool["input_schema"]
            for name, arguments in sent:
                result = await client.call_tool(name, arguments)
                assert not result.is_error, name

    anyio.run(exercise)

    expected = [("tumnis", name, arguments) for name, arguments in sent]
    assert [(c.server, c.tool, dict(c.arguments)) for c in recorder.calls] == expected
    lines = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert [(r["server"], r["tool"], r["arguments"]) for r in lines] == expected
