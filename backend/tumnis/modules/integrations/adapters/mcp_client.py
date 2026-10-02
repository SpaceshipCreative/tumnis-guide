"""`McpSource`: the MCP client wrapper a connector reads its provider through (P3-02,
FR-14.9).

A call is refused with `ToolNotAllowed` unless the source's allow-list names the tool,
before any I/O: the access token is not asked for and no request is sent. Every
provider's built-in list (`TOOL_ALLOWLISTS`) holds read tools and at most a draft tool,
never one that sends anything. An allowed call opens a Streamable HTTP session (MCP SDK
1.30.0, `streamable_http_client(url, http_client=...)`) over `core.net.guarded_client`
(SSRF checks on every request, no redirects, no proxy variables) with the access token
as a bearer header, initializes, calls the tool and closes.

`token` is an async callable answering a live access token (the sync passes one over
`integrations.api.access_token`, which refreshes under the connection row's lock). An
HTTP 401 raises `ReauthRequired`; an unreachable server, a 5xx or a tool error raises
`AdapterUnavailable`.

The tool names below are assumptions until P3-01 records the providers' real tool lists
(docs/plan/p3-02-provider-assumptions.md).
"""

from collections.abc import Awaitable, Callable, Iterator, Mapping
from typing import Any, Final

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tumnis.core.adapters.errors import AdapterUnavailable
from tumnis.core.net import NetPolicy, Resolver, guarded_client, system_resolver
from tumnis.modules.integrations.api import ReauthRequired, connector_adapter_name

TIMEOUT_S: Final = 30.0  # plan default for one tool call

TOOL_ALLOWLISTS: Final[Mapping[str, frozenset[str]]] = {
    # The framework's scriptable provider.
    "fake": frozenset({"list_messages", "read_message"}),
    # Inbox Zero (P3-03): search and read, plus the one write, a draft (never a send).
    "inbox_zero": frozenset({"search_inbox", "read_thread", "create_draft"}),
    # Granola (P3-04): the two tools its Basic plan offers.
    "granola": frozenset({"list_meetings", "get_meetings"}),
}

TokenSource = Callable[[], Awaitable[str]]


class ToolNotAllowed(Exception):  # noqa: N818  # the plan's name
    def __init__(self, tool: str) -> None:
        super().__init__(tool)
        self.tool = tool


class McpSource:
    def __init__(  # the wrapper's dependencies, all keyword-only
        self,
        *,
        provider: str,
        server_url: str,
        token: TokenSource,
        allowed_tools: frozenset[str] | None = None,
        policy: NetPolicy | None = None,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        """`allowed_tools` narrows the provider's built-in list (never widens it: a tool
        must be in both). `policy` defaults to the hosted policy; `transport` replaces the
        network (tests)."""
        builtin = TOOL_ALLOWLISTS.get(provider, frozenset())
        self.provider = provider
        self.server_url = server_url
        self.allowed = builtin if allowed_tools is None else allowed_tools & builtin
        self._token = token
        self._policy = policy or NetPolicy(mode="hosted")
        self._resolver = resolver
        self._transport = transport
        self._timeout_s = timeout_s

    async def call(self, tool: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """The tool's structured result (or `{"content": [...]}` when it has none)."""
        if tool not in self.allowed:
            raise ToolNotAllowed(tool)
        token = await self._token()
        adapter = connector_adapter_name(self.provider)
        http = guarded_client(
            self._policy, timeout=self._timeout_s, resolver=self._resolver, inner=self._transport
        )
        http.headers["Authorization"] = f"Bearer {token}"
        try:
            async with (
                http,
                streamable_http_client(self.server_url, http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(tool, dict(args))
        except Exception as exc:
            status = _http_status(exc)
            if status == 401:  # noqa: PLR2004
                raise ReauthRequired(self.provider, "HTTP 401") from None
            detail = f"HTTP {status}" if status is not None else type(exc).__name__
            raise AdapterUnavailable(adapter, tool, detail) from None
        if result.isError:
            raise AdapterUnavailable(adapter, tool, "the tool answered an error")
        if result.structuredContent is not None:
            return result.structuredContent
        return {"content": [block.model_dump(mode="json") for block in result.content]}


def _causes(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        stack.extend(e for e in (current.__cause__, current.__context__) if e is not None)


def _http_status(exc: BaseException) -> int | None:
    """The HTTP status behind a failed call, wherever the SDK's task group wrapped it."""
    for cause in _causes(exc):
        if isinstance(cause, httpx.HTTPStatusError):
            return cause.response.status_code
    return None
