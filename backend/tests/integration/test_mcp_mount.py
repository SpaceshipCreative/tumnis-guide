"""`/mcp` is mounted as a raw route and served by the session manager the lifespan
started (P2-01, FR-14.10).

A `Mount("/mcp")` would redirect `POST /mcp` to `/mcp/`, which httpx does not follow on
POST; and a session manager nobody started fails the first request. The Streamable HTTP
transport answers every request with one JSON body (stateless, JSON responses).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

INITIALIZE = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "tumnis-tests", "version": "1"},
}


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
async def test_post_mcp_is_not_redirected(app: FastAPI, key_client: Any) -> None:
    """T-P2-01-18
    `POST /mcp` initialize (no trailing slash) answers 200 with a JSON body carrying the
    server's name and its tools capability, never a 307."""
    from tests._mcp import http_for, mcp_running, rpc  # noqa: PLC0415

    keyed = await key_client(frozenset({"tasks:read"}))
    async with mcp_running(app), http_for(app, keyed.key) as http:
        response = await rpc(http, "initialize", INITIALIZE)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    result = response.json()["result"]
    assert result["serverInfo"]["name"] == "tumnis"
    assert "tools" in result["capabilities"]


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
async def test_first_request_after_startup_succeeds(app: FastAPI, key_client: Any) -> None:
    """T-P2-01-19
    With only the app's own lifespan run (nothing else starts the session manager), the
    first `tools/list` after startup answers 200 with the registered tools."""
    from tests._mcp import http_for, rpc  # noqa: PLC0415

    keyed = await key_client(frozenset({"tasks:read"}))
    async with app.router.lifespan_context(app), http_for(app, keyed.key) as http:
        response = await rpc(http, "tools/list", {})
    assert response.status_code == 200, response.text
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert "list_tasks" in names
