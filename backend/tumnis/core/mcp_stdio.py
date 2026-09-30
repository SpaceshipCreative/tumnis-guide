"""`tumnis mcp-stdio`: a stdio MCP server that forwards every line to `/mcp` (P2-01, FR-14.10).

For an MCP client that only speaks stdio. Each JSON-RPC line read from stdin is one POST to
`/mcp` (MCP Streamable HTTP, spec 2025-11-25, "Sending Messages to the Server"), carrying
`Authorization: Bearer <TUMNIS_API_KEY>` and `Accept: application/json, text/event-stream`,
so the caller's key and scopes apply exactly as over HTTP. The answer goes back as one line
on stdout:

- 202 or an empty body (a notification or a response): nothing;
- a JSON body: that message, compact, on one line;
- an SSE body: one line per `data:` event;
- a non-2xx answer to a request: a JSON-RPC error line for that request's `id` (code
  -32000; `data` is the problem the server answered), so the client is never left waiting;
- the server unreachable: the same, with the transport error as the message;
- a 2xx body that is not JSON (a proxy's page, a torn SSE event): the same.

After `initialize`, later requests carry `MCP-Protocol-Version` with the negotiated
version, and an `Mcp-Session-Id` the server assigned (none today: `/mcp` is stateless) is
sent back. Stdout carries only MCP messages; anything else goes to stderr.
"""

import asyncio
import json
import sys
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any, Final, TextIO

import httpx

from tumnis.core.net import operator_client

MCP_PATH: Final = "/mcp"
DEFAULT_URL: Final = "http://127.0.0.1:8000"
ACCEPT: Final = "application/json, text/event-stream"
SERVER_ERROR: Final = -32000  # JSON-RPC implementation-defined server error
PARSE_ERROR: Final = -32700
TIMEOUT: Final = httpx.Timeout(300.0, connect=10.0)  # a tool call may take a while


def _emit(out: TextIO, message: Any) -> None:
    out.write(json.dumps(message, separators=(",", ":")) + "\n")
    out.flush()


def _error(request_id: Any, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": SERVER_ERROR, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def sse_messages(body: str) -> list[Any]:
    """The JSON messages of an SSE body: one per event, from its `data:` lines."""
    messages: list[Any] = []
    data: list[str] = []
    for line in [*body.splitlines(), ""]:
        if line.startswith("data:"):
            data.append(line.removeprefix("data:").removeprefix(" "))
        elif not line and data:
            messages.append(json.loads("\n".join(data)))
            data = []
    return messages


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text or None


class _Session:
    """The headers every POST carries, and what the server's answers add to them."""

    def __init__(self, key: str) -> None:
        self.headers = {
            "Authorization": f"Bearer {key}",
            "Accept": ACCEPT,
            "Content-Type": "application/json",
        }

    def learn(self, response: httpx.Response, messages: list[Any]) -> None:
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id
        for message in messages:
            result = message.get("result") if isinstance(message, dict) else None
            version = result.get("protocolVersion") if isinstance(result, dict) else None
            if isinstance(version, str):
                self.headers["MCP-Protocol-Version"] = version


def _fail(out: TextIO, request_id: Any, message: str, data: Any = None) -> None:
    """A request gets a JSON-RPC error line; a notification, a note on stderr."""
    if request_id is not None:
        _emit(out, _error(request_id, message, data))
    else:
        print(f"tumnis mcp-stdio: {message}", file=sys.stderr)


def _messages(response: httpx.Response) -> list[Any]:
    """The JSON-RPC messages of a 2xx answer; ValueError when the body is not JSON."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return sse_messages(response.text)
    return [response.json()]


async def _relay(
    line: str, out: TextIO, http: httpx.AsyncClient, session: _Session, url: str
) -> None:
    try:
        message = json.loads(line)
    except ValueError:
        _emit(
            out,
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": PARSE_ERROR, "message": "Parse error"},
            },
        )
        return
    request_id = message.get("id") if isinstance(message, dict) else None
    try:
        response = await http.post(url, content=line.strip(), headers=session.headers)
    except httpx.HTTPError as exc:
        _fail(out, request_id, f"Tumnis is unreachable: {exc!r}")
        return
    if not response.is_success:
        body = _body(response)
        title = body.get("title") if isinstance(body, dict) else None
        _fail(out, request_id, str(title or f"HTTP {response.status_code}"), body)
        return
    if response.status_code == httpx.codes.ACCEPTED or not response.content:
        return
    try:
        messages = _messages(response)
    except ValueError:  # a 2xx that is not JSON (a proxy's page, a torn event)
        _fail(out, request_id, "Tumnis answered with a body that is not JSON")
        return
    session.learn(response, messages)
    for answer in messages:
        _emit(out, answer)


async def forward(
    lines: AsyncIterable[str],
    out: TextIO,
    http: httpx.AsyncClient,
    *,
    key: str,
    url: str = MCP_PATH,
) -> None:
    """POST each non-empty line to `url` on `http` with the caller's key, one at a time,
    and write each answer to `out` as described above."""
    session = _Session(key)
    async for line in lines:
        if line.strip():
            await _relay(line, out, http, session, url)


async def stdin_lines() -> AsyncIterator[str]:
    """Lines from stdin, read off the event loop, until end of file."""
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        yield line


async def run(*, key: str, base_url: str) -> None:
    """Forward stdin to `<base_url>/mcp` until stdin closes."""
    async with operator_client(base_url, timeout=TIMEOUT) as http:
        await forward(stdin_lines(), sys.stdout, http, key=key)
