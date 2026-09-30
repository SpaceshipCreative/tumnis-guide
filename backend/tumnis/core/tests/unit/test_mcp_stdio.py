"""The stdio shim's edge cases (P2-01, FR-14.10), against `httpx.MockTransport`: what each
kind of `/mcp` answer becomes on stdout, and where a bearer key may be sent.

The end-to-end relay through the real `/mcp` mount is T-P2-01-12 (integration)."""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from tumnis.core import mcp_stdio
from tumnis.core.net import check_operator_url

pytestmark = [pytest.mark.req("FR-14.10"), pytest.mark.wp("P2-01")]

KEY = "tmn_test_key"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
NOTE = {"jsonrpc": "2.0", "method": "notifications/initialized"}


async def _lines(*items: Any) -> AsyncIterator[str]:
    for item in items:
        yield (item if isinstance(item, str) else json.dumps(item)) + "\n"


async def _run(
    handler: Callable[[httpx.Request], httpx.Response], *items: Any
) -> tuple[list[Any], list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    out = io.StringIO()
    transport = httpx.MockTransport(record)
    async with httpx.AsyncClient(transport=transport, base_url="https://tumnis.test") as http:
        await mcp_stdio.forward(_lines(*items), out, http, key=KEY)
    return [json.loads(line) for line in out.getvalue().splitlines()], seen


def _result(request: httpx.Request, result: Any) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


async def test_json_answer_is_one_compact_line_and_notification_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if b'"id"' not in request.content:
            return httpx.Response(202)
        return _result(request, {"ok": True})

    answers, seen = await _run(handler, INIT, NOTE, "", LIST)
    assert [a["id"] for a in answers] == [1, 2]
    assert len(seen) == 3  # the blank line is not sent
    assert all(r.url.path == "/mcp" for r in seen)
    assert all(r.headers["authorization"] == f"Bearer {KEY}" for r in seen)
    assert all(r.headers["content-type"] == "application/json" for r in seen)


async def test_negotiated_protocol_version_and_session_id_are_sent_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            response = _result(request, {"protocolVersion": "2025-06-18"})
            response.headers["Mcp-Session-Id"] = "s-1"
            return response
        return _result(request, {})

    _, seen = await _run(handler, INIT, LIST)
    assert "mcp-protocol-version" not in seen[0].headers
    assert seen[1].headers["mcp-protocol-version"] == "2025-06-18"
    assert seen[1].headers["mcp-session-id"] == "s-1"


async def test_sse_answer_is_one_line_per_event() -> None:
    events = [
        {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}},
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
    ]
    body = "".join(f"event: message\ndata: {json.dumps(e)}\n\n" for e in events)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    answers, _ = await _run(handler, LIST)
    assert answers == events


async def test_refused_request_becomes_a_json_rpc_error_for_its_id() -> None:
    problem = {"status": 401, "code": "unauthenticated", "title": "Sign in"}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json=problem)

    answers, _ = await _run(handler, NOTE, LIST)
    assert answers == [
        {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": mcp_stdio.SERVER_ERROR, "message": "Sign in", "data": problem},
        }
    ]


async def test_unreachable_server_answers_each_request_with_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    answers, _ = await _run(handler, NOTE, LIST)
    assert [(a["id"], a["error"]["code"]) for a in answers] == [(2, mcp_stdio.SERVER_ERROR)]


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("<html>proxy error</html>", "text/html"),
        ("event: message\ndata: {not json\n\n", "text/event-stream"),
    ],
)
async def test_a_2xx_body_that_is_not_json_answers_the_request_and_the_shim_goes_on(
    body: str, content_type: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["id"] == 1:
            return httpx.Response(200, text=body, headers={"content-type": content_type})
        return _result(request, {"ok": True})

    answers, _ = await _run(handler, INIT, LIST)
    assert [(a["id"], "error" in a) for a in answers] == [(1, True), (2, False)]
    assert answers[0]["error"]["code"] == mcp_stdio.SERVER_ERROR


async def test_a_line_that_is_not_json_is_a_parse_error_and_not_sent() -> None:
    answers, seen = await _run(lambda r: _result(r, {}), "{not json")
    assert answers == [
        {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    ]
    assert seen == []


@pytest.mark.parametrize(
    "url",
    [
        "https://tumnis.example.com",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://192.168.1.20:8000",
        "http://[::1]:8000",
    ],
)
def test_a_key_may_go_over_https_or_to_a_near_http_address(url: str) -> None:
    check_operator_url(url)


@pytest.mark.parametrize(
    "url", ["http://tumnis.example.com", "http://93.184.216.34", "ftp://127.0.0.1", "/mcp"]
)
def test_a_key_never_goes_over_plain_http_to_a_far_address(url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        check_operator_url(url)
