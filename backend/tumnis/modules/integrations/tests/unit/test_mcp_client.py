"""The MCP client wrapper's tool allow-list (P3-02, FR-14.9): a connector may call only the
tools its provider's list names; anything else is refused before any I/O."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-14.9")
@pytest.mark.wp("P3-02")
async def test_tool_allowlist_blocks_unlisted_tools() -> None:
    """T-P3-02-13
    `McpSource.call("send_email")` on a source whose allow-list holds only read tools
    raises `ToolNotAllowed` before any I/O: the token is never asked for and the HTTP
    transport sees no request. Every provider's built-in list is read-only or drafts
    only: no tool whose name sends anything.
    """
    import httpx  # noqa: PLC0415

    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.modules.integrations.adapters.mcp_client import (  # noqa: PLC0415
        TOOL_ALLOWLISTS,
        McpSource,
        ToolNotAllowed,
    )

    requests: list[httpx.Request] = []
    tokens_asked: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    async def token() -> str:
        tokens_asked.append(True)
        return "fake-access-token"

    source = McpSource(
        provider="inbox_zero",
        server_url="https://mail.example.com/mcp",
        allowed_tools=frozenset({"search_inbox", "read_thread"}),
        token=token,
        policy=NetPolicy(mode="self-hosted"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ToolNotAllowed) as refused:
        await source.call("send_email", {"to": "person-1@client-1.example", "body": "hi"})
    assert refused.value.tool == "send_email"
    assert requests == []
    assert tokens_asked == []

    for provider, tools in TOOL_ALLOWLISTS.items():
        assert tools, provider
        assert not {tool for tool in tools if tool.startswith("send")}, provider
