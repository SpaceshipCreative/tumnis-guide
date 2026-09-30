"""`tumnis mcp-stdio`: a line forwarder from a stdio MCP client to `/mcp` with the
caller's key (P2-01, FR-14.10).

Each JSON-RPC line read from stdin is one POST to `/mcp` carrying `Authorization: Bearer`
the key from `TUMNIS_API_KEY`; each JSON answer is one line on stdout. A notification (no
`id`) gets no line back. Without a key the command writes why on stderr and exits 2,
leaving stdout empty (a stdio client reads only MCP messages there).
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx
import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

LINES: list[dict[str, Any]] = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "stdio-test", "version": "1"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "list_tasks", "arguments": {"limit": 5}},
    },
]


async def _lines(items: list[dict[str, Any]]) -> AsyncIterator[str]:
    for item in items:
        yield json.dumps(item) + "\n"


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
async def test_shim_forwards_with_callers_key(app: FastAPI, key_client: Any) -> None:
    """T-P2-01-12
    Given `forward(stdin, stdout, http, key=...)` over an in-process client, when
    initialize, a notification, tools/list and a list_tasks call are fed, then three JSON
    lines come out (ids 1, 2, 3), every request carried the bearer key and the Streamable
    HTTP Accept header, and the notification produced no line."""
    from tests._mcp import mcp_running  # noqa: PLC0415
    from tumnis.core import mcp_stdio  # noqa: PLC0415

    keyed = await key_client(frozenset({"tasks:read"}))
    seen: list[httpx.Request] = []

    async def record(request: httpx.Request) -> None:
        seen.append(request)

    out = io.StringIO()
    async with (
        mcp_running(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
            event_hooks={"request": [record]},
        ) as http,
    ):
        await mcp_stdio.forward(_lines(LINES), out, http, key=keyed.key)
    answers = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [a["id"] for a in answers] == [1, 2, 3]
    assert "list_tasks" in {t["name"] for t in answers[1]["result"]["tools"]}
    assert answers[2]["result"]["isError"] is False
    assert len(seen) == len(LINES)
    for request in seen:
        assert request.headers["authorization"] == f"Bearer {keyed.key}"
        accept = request.headers["accept"]
        assert "application/json" in accept
        assert "text/event-stream" in accept


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
def test_shim_without_key_exits_2_on_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P2-01-13
    `tumnis mcp-stdio` with no `TUMNIS_API_KEY` exits 2 with the reason on stderr and
    nothing on stdout."""
    from typer.testing import CliRunner  # noqa: PLC0415

    from tumnis.cli import app as cli  # noqa: PLC0415

    monkeypatch.delenv("TUMNIS_API_KEY", raising=False)
    result = CliRunner().invoke(cli, ["mcp-stdio"], input="")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "TUMNIS_API_KEY" in result.stderr
