"""`tumnis mcp-stdio`: a stdio MCP server that forwards every line to `/mcp` (P2-01, FR-14.10).

For an MCP client that only speaks stdio. Each JSON-RPC line read from stdin is one POST to
`/mcp` (MCP Streamable HTTP, spec 2025-11-25, "Sending Messages to the Server"), carrying
`Authorization: Bearer <TUMNIS_API_KEY>` and `Accept: application/json, text/event-stream`,
so the caller's key and scopes apply exactly as over HTTP. The answer goes back on stdout:

- 202 or an empty body (a notification or a response): nothing;
- a JSON body: that message, compact, on one line;
- an SSE body: one line per `data:` event, written as soon as the event is complete (so
  progress reaches the client while the call runs); an event with empty data, such as the
  stream's priming event, is skipped;
- a non-2xx answer to a request: a JSON-RPC error line for that request's `id` (code
  -32000; `data` is the problem the server answered), so the client is never left waiting;
- the server unreachable, or an SSE stream that ends before the answer: the same, with the
  reason as the message;
- a 2xx body that is not JSON (a proxy's page, a torn SSE event): the same.

Requests are relayed one at a time, in the order they were read, so answers come back in
that order. Reading stdin never waits for them: a notification (such as
`notifications/cancelled`) or a response is forwarded as soon as it is read, and a request
cancelled while it still waits its turn is dropped unsent. `initialize` is relayed before
anything read after it. After `initialize`, later requests carry `MCP-Protocol-Version`
with the negotiated version, and an `Mcp-Session-Id` the server assigned (none today:
`/mcp` is stateless) is sent back. Stdout carries only MCP messages; anything else goes to
stderr.
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


async def sse_data(lines: AsyncIterable[str]) -> AsyncIterator[str]:
    """The data of each complete SSE event in `lines` (lines without their endings), as
    soon as the blank line ending it is read. An event whose data is empty (MCP's priming
    event: an id and an empty `data:`) yields nothing, and so does an event the stream
    ends before completing (WHATWG HTML, "Interpreting an event stream")."""
    data: list[str] = []
    async for line in lines:
        if line.startswith("data:"):
            data.append(line.removeprefix("data:").removeprefix(" "))
        elif not line:
            joined = "\n".join(data)
            data = []
            if joined.strip():
                yield joined


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text or None


def _request_id(message: Any) -> Any:
    """The id a JSON-RPC request is answered under; None for a notification or a response."""
    if isinstance(message, dict) and "method" in message:
        return message.get("id")
    return None


def _answers(message: Any, request_id: Any) -> bool:
    return (
        isinstance(message, dict)
        and "method" not in message
        and message.get("id") == request_id
        and ("result" in message or "error" in message)
    )


class _Session:
    """The headers every POST carries, and what the server's answers add to them."""

    def __init__(self, key: str) -> None:
        self.headers = {
            "Authorization": f"Bearer {key}",
            "Accept": ACCEPT,
            "Content-Type": "application/json",
        }

    def learn_headers(self, response: httpx.Response) -> None:
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id

    def learn(self, message: Any) -> None:
        result = message.get("result") if isinstance(message, dict) else None
        version = result.get("protocolVersion") if isinstance(result, dict) else None
        if isinstance(version, str):
            self.headers["MCP-Protocol-Version"] = version


def _fail(out: TextIO, request_id: Any, message: str, data: Any = None) -> None:
    """A request gets a JSON-RPC error line; anything else, a note on stderr."""
    if request_id is not None:
        _emit(out, _error(request_id, message, data))
    else:
        print(f"tumnis mcp-stdio: {message}", file=sys.stderr)


async def _relay(
    line: str,
    message: Any,
    out: TextIO,
    *,
    http: httpx.AsyncClient,
    session: _Session,
    url: str,
) -> None:
    request_id = _request_id(message)
    answered = False

    def emit(answer: Any) -> None:
        nonlocal answered
        session.learn(answer)
        _emit(out, answer)
        answered = answered or _answers(answer, request_id)

    try:
        async with http.stream(
            "POST", url, content=line.strip(), headers=session.headers
        ) as response:
            if not response.is_success:
                await response.aread()
                body = _body(response)
                title = body.get("title") if isinstance(body, dict) else None
                _fail(out, request_id, str(title or f"HTTP {response.status_code}"), body)
                return
            session.learn_headers(response)
            if response.headers.get("content-type", "").startswith("text/event-stream"):
                async for data in sse_data(response.aiter_lines()):
                    emit(json.loads(data))
            else:
                content = await response.aread()
                if response.status_code == httpx.codes.ACCEPTED or not content:
                    return
                emit(json.loads(content))
                return
    except httpx.HTTPError as exc:
        _fail(out, None if answered else request_id, f"Tumnis is unreachable: {exc!r}")
        return
    except ValueError:  # a 2xx that is not JSON (a proxy's page, a torn event)
        _fail(out, None if answered else request_id, "Tumnis answered with a body that is not JSON")
        return
    if request_id is not None and not answered:
        _fail(out, request_id, "Tumnis ended the stream without an answer")


async def forward(
    lines: AsyncIterable[str],
    out: TextIO,
    http: httpx.AsyncClient,
    *,
    key: str,
    url: str = MCP_PATH,
) -> None:
    """POST each non-empty line to `url` on `http` with the caller's key, and write each
    answer to `out`, as described above. Returns once `lines` ends and every request read
    has been answered."""
    session = _Session(key)
    queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
    cancelled: set[str | int] = set()

    async def send_requests() -> None:
        while (item := await queue.get()) is not None:
            line, message = item
            request_id = message.get("id")
            if isinstance(request_id, str | int) and request_id in cancelled:
                continue  # cancelled before it was sent: nothing to send or answer
            await _relay(line, message, out, http=http, session=session, url=url)

    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(send_requests())
        try:
            async for line in lines:
                if not line.strip():
                    continue
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
                    continue
                method = message.get("method") if isinstance(message, dict) else None
                if _request_id(message) is not None and method != "initialize":
                    queue.put_nowait((line, message))
                    continue
                if method == "notifications/cancelled":
                    params = message.get("params")
                    target = params.get("requestId") if isinstance(params, dict) else None
                    if isinstance(target, str | int):
                        cancelled.add(target)
                await _relay(line, message, out, http=http, session=session, url=url)
        finally:
            queue.put_nowait(None)


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
