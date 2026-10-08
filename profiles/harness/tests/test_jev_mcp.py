"""The profiles' System One MCP server, hardened (FIX-jev-mcp, P1-05, FR-11.6).

`profiles/shared/jev-mcp` is how Hermes skills ask typed questions of a System One
endpoint: TypeSafe's Jev by default, or any endpoint that speaks the same API
(`POST /v1/systemone`), such as Cloudflare's Clef or Clef-Flash served on the user's own
inference VM (Scott decision 102). The MCP server keeps its historical name, `jev`.

These tests hold it to what the backend adapter (`decisions/adapters/jev.py`, P1-01)
already guarantees for Tumnis's own calls: one SDK client per run, closed at shutdown; a
bounded call; failures the agent can branch on (MCP turns any other exception into an
opaque `Error executing tool ask` and logs its traceback, message included); refusals
before anything is sent; and logs that carry typed facts, never the state or the question
text (Data flow rule 6).

The endpoint comes from the profile's .env through the TypeSafe SDK's own variables:
`TYPESAFE_BASE_URL` (default TypeSafe's API), `TYPESAFE_DEFAULT_MODEL` (default the pinned
Jev model) and `TYPESAFE_API_KEY` (required only for TypeSafe's API). Its limits come from
`SYSTEMONE_MAX_REQUEST_TOKENS` and `SYSTEMONE_MAX_STATE_TOKENS` (default Jev's margins).
An empty variable means its default. A model must be a pinned version only on TypeSafe's
API; another endpoint's model is whatever id it serves (Scott decision 102c).

A fake endpoint answers over `httpx2.MockTransport`; nothing reaches the network. The
seam is `jev_mcp.server.create_server(transport=..., timeout_s=...)`, which the fix adds;
it reads the variables above when called.

Codes: `provider_unavailable` (connection, 429, 5xx: retryable), `provider_rejected`
(any other 4xx), `provider_timeout`, `model_not_pinned`, `decision_request_too_large` and
`invalid_question`. An error's text holds `<code>: `.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from harness import REPO

pytestmark = [pytest.mark.req("FR-11.6"), pytest.mark.wp("P1-05")]

PINNED = "jev-1.13.0"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
LOCAL_BASE = "http://10.20.0.5:8000"  # a System One server on the user's inference VM
STATE_CANARY = "CANARY-STATE-7f3e"
QUESTION_CANARY = "CANARY-QUESTION-9b2c"
SPEC = "spec:FIX-jev-mcp"
SERVER_ENV = (  # what the server reads; cleared so a developer's own values never leak in
    "TYPESAFE_API_KEY",
    "TYPESAFE_BASE_URL",
    "TYPESAFE_DEFAULT_MODEL",
    "SYSTEMONE_MAX_REQUEST_TOKENS",
    "SYSTEMONE_MAX_STATE_TOKENS",
)

Handler = Callable[[httpx2.Request], httpx2.Response | Awaitable[httpx2.Response]]


def _answered(request: httpx2.Request) -> httpx2.Response:
    """A System One 200 in the SDK 0.7.2 wire shape: a Noul per question asked, from the
    model asked."""
    asked = json.loads(request.content)
    return httpx2.Response(
        200,
        headers={"x-typesafe-request-id": "req_test_0001"},
        json={
            "model": asked["model"],
            "usage": {"input_tokens": 161, "output_tokens": 1},
            "answers": {qid: {"type": "noul", "noul": 0.97} for qid in asked["questions"]},
        },
    )


class FakeEndpoint(httpx2.MockTransport):
    """Records every request that reaches it and every time it is closed (the SDK client
    closes the transport it was given when the client closes)."""

    def __init__(self, handler: Handler = _answered) -> None:
        self.requests: list[httpx2.Request] = []
        self.closed = 0

        async def record(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            response = handler(request)
            return response if isinstance(response, httpx2.Response) else await response

        super().__init__(record)

    async def aclose(self) -> None:
        self.closed += 1
        await super().aclose()


def _args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "state": {"action_summary": f"Deploy the Acme site to production {STATE_CANARY}"},
        "questions": {
            "gated": {
                "type": "noul",
                "instructions": f"The action is gated by the policy. {QUESTION_CANARY}",
                "criteria": {"true": "Gated.", "false": "Not gated."},
            },
            "reversible": {"type": "noul", "instructions": "The action can be undone."},
        },
    }
    return {**args, **overrides}


def _error_text(result: CallToolResult) -> str:
    assert result.is_error, "the call should end as a tool error"
    return " ".join(part.text for part in result.content if isinstance(part, TextContent))


def _payload(result: CallToolResult) -> Any:
    assert not result.is_error, _error_text(result) if result.is_error else ""
    text = "".join(part.text for part in result.content if isinstance(part, TextContent))
    return json.loads(text)


def _server(**kwargs: Any) -> MCPServer:
    """`create_server(**kwargs)`, imported at call time so a missing seam fails the test,
    not the collection. The ignore stays valid once the seam exists (`unused-ignore`)."""
    from jev_mcp import server

    factory: Callable[..., MCPServer] = server.create_server  # type: ignore[attr-defined, unused-ignore]
    return factory(**kwargs)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """A clean server environment holding only a TypeSafe key."""
    for name in SERVER_ENV:
        monkeypatch.delenv(name, raising=False)
    key = "tsk-test-0000-not-a-real-key"
    monkeypatch.setenv("TYPESAFE_API_KEY", key)
    return key


@pytest.mark.anyio
@pytest.mark.xfail(strict=True, reason=SPEC)
async def test_one_client_serves_every_call_and_closes_at_shutdown() -> None:
    """FIX-jev-mcp-01: one SDK client serves every call of a server run and is closed once,
    when the server stops (today each call opens a client and never closes it). By default
    the calls go to TypeSafe's API with the pinned Jev model, and answers come back as the
    endpoint reports them."""
    fake = FakeEndpoint()
    async with Client(_server(transport=fake)) as client:
        first = await client.call_tool("ask", _args())
        second = await client.call_tool("ask", _args())
        assert fake.closed == 0
    assert _payload(first) == _payload(second)
    assert _payload(first) == {
        "model": PINNED,
        "usage": {"input_tokens": 161, "output_tokens": 1},
        "answers": {
            "gated": {"type": "noul", "noul": 0.97},
            "reversible": {"type": "noul", "noul": 0.97},
        },
    }
    assert [str(r.url) for r in fake.requests] == [TYPESAFE_URL, TYPESAFE_URL]
    assert [json.loads(r.content)["model"] for r in fake.requests] == [PINNED, PINNED]
    assert fake.closed == 1


@pytest.mark.anyio
@pytest.mark.xfail(strict=True, reason=SPEC)
async def test_a_local_system_one_endpoint_serves_the_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FIX-jev-mcp-02: with `TYPESAFE_BASE_URL` and `TYPESAFE_DEFAULT_MODEL` set, calls go
    to that endpoint with that model, unversioned ids included (Clef-Flash on the user's
    inference VM, Scott decision 102); such an endpoint needs no key, and a plain-http one
    is fine on a private address."""
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("TYPESAFE_BASE_URL", LOCAL_BASE)
    monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "clef-flash")
    fake = FakeEndpoint()
    async with Client(_server(transport=fake)) as client:
        result = await client.call_tool("ask", _args())
    assert _payload(result)["model"] == "clef-flash"
    assert [str(r.url) for r in fake.requests] == [f"{LOCAL_BASE}/v1/systemone"]
    assert json.loads(fake.requests[0].content)["model"] == "clef-flash"


@pytest.mark.anyio
@pytest.mark.xfail(strict=True, reason=SPEC)
async def test_a_call_that_hangs_ends_as_provider_timeout() -> None:
    """FIX-jev-mcp-03: every call is bounded by the server's timeout (today none is set):
    an endpoint that never answers ends the call as `provider_timeout` once it passes."""

    async def hang(request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep(30)
        return _answered(request)

    started = time.monotonic()
    with anyio.fail_after(10):
        async with Client(_server(transport=FakeEndpoint(hang), timeout_s=0.2)) as client:
            result = await client.call_tool("ask", _args())
    assert "provider_timeout:" in _error_text(result)
    assert time.monotonic() - started < 5


def _refuse(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "code"),
    [
        pytest.param(
            lambda r: httpx2.Response(429, headers={"retry-after-ms": "1500"}, json={}),
            "provider_unavailable",
            id="rate-limited",
        ),
        pytest.param(lambda r: httpx2.Response(503, json={}), "provider_unavailable", id="5xx"),
        pytest.param(_refuse, "provider_unavailable", id="connection-refused"),
        pytest.param(lambda r: httpx2.Response(401, json={}), "provider_rejected", id="401"),
        pytest.param(lambda r: httpx2.Response(400, json={}), "provider_rejected", id="400"),
    ],
)
@pytest.mark.xfail(strict=True, reason=SPEC)
async def test_failures_reach_the_agent_as_stable_codes(handler: Handler, code: str) -> None:
    """FIX-jev-mcp-04: a failed call reaches the agent as a stable code it can branch on,
    not `Error executing tool ask`. One attempt per call: the SDK's own retries are off,
    so a rate-limited key is not hit again behind the agent's back."""
    fake = FakeEndpoint(handler)
    async with Client(_server(transport=fake)) as client:
        result = await client.call_tool("ask", _args())
    assert f"{code}:" in _error_text(result)
    assert len(fake.requests) == 1


@pytest.mark.anyio
@pytest.mark.xfail(strict=True, reason=SPEC)
async def test_a_rate_limit_says_when_to_retry() -> None:
    """FIX-jev-mcp-05: a 429 carries the endpoint's own wait (`retry-after-ms`), in
    seconds."""
    fake = FakeEndpoint(lambda r: httpx2.Response(429, headers={"retry-after-ms": "1500"}, json={}))
    async with Client(_server(transport=fake)) as client:
        result = await client.call_tool("ask", _args())
    assert "retry_after_s=1.5" in _error_text(result)


@pytest.mark.anyio
@pytest.mark.req("FR-11.2")
@pytest.mark.parametrize("model", ["jev-latest", "jev-1.13", "jev"])
@pytest.mark.xfail(strict=True, reason=SPEC)
async def test_an_unpinned_model_is_refused_on_typesafes_api(model: str) -> None:
    """FIX-jev-mcp-06: on TypeSafe's API, a model alias is refused as `model_not_pinned`
    (today a ValueError, which the agent sees only as `Error executing tool ask`); nothing
    is sent. Another endpoint takes the ids it serves (FIX-jev-mcp-02)."""
    fake = FakeEndpoint()
    async with Client(_server(transport=fake)) as client:
        result = await client.call_tool("ask", _args(model=model))
    assert "model_not_pinned:" in _error_text(result)
    assert fake.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "question",
    [
        pytest.param({"type": "verdict", "instructions": QUESTION_CANARY}, id="unknown-type"),
        pytest.param({"type": "score", "instructions": QUESTION_CANARY}, id="score-no-levels"),
        pytest.param(
            {"type": "choice", "instructions": QUESTION_CANARY, "criteria": "a or b"},
            id="choice-criteria-not-a-map",
        ),
    ],
)
@pytest.mark.xfail(strict=True, reason=SPEC)
async def test_a_malformed_question_is_refused_without_echoing_it(
    question: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """FIX-jev-mcp-07: a question the API cannot take is refused as `invalid_question`,
    naming its id but never its text, in the answer or in any log; nothing is sent."""
    caplog.set_level(logging.DEBUG)
    fake = FakeEndpoint()
    async with Client(_server(transport=fake)) as client:
        result = await client.call_tool("ask", _args(questions={"kind": question}))
    text = _error_text(result)
    assert "invalid_question:" in text
    assert "kind" in text
    assert QUESTION_CANARY not in text
    assert QUESTION_CANARY not in caplog.text
    assert fake.requests == []


@pytest.mark.anyio
@pytest.mark.req("FR-11.9")
@pytest.mark.parametrize(
    ("limits", "state"),
    [
        pytest.param({}, "x" * 300_000, id="default-request-over-60k-tokens"),
        pytest.param({}, "x" * 140_000, id="default-state-plus-question-over-30k-tokens"),
        pytest.param(
            {"SYSTEMONE_MAX_REQUEST_TOKENS": "16384"},
            "x" * 80_000,
            id="configured-request-over-16k-tokens",
        ),
        pytest.param(
            {"SYSTEMONE_MAX_STATE_TOKENS": "8000"},
            "x" * 40_000,
            id="configured-state-plus-question-over-8k-tokens",
        ),
    ],
)
@pytest.mark.xfail(strict=True, reason=SPEC)
async def test_an_oversized_request_is_refused_before_sending(
    limits: dict[str, str], state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIX-jev-mcp-08: a request over the endpoint's limits is refused as
    `decision_request_too_large` before anything is sent, as the backend's `build_request`
    refuses one (tokens estimated as UTF-8 bytes / 4). The limits default to Jev's margins
    (60,000 tokens per request, 30,000 for the state plus its largest question) and are
    set per endpoint (a local Clef-Flash takes 16,384 by default)."""
    for name, value in limits.items():
        monkeypatch.setenv(name, value)
    fake = FakeEndpoint()
    async with Client(_server(transport=fake)) as client:
        result = await client.call_tool("ask", _args(state=state))
    assert "decision_request_too_large:" in _error_text(result)
    assert fake.requests == []


def _all_text(record: logging.LogRecord) -> str:
    return " ".join(
        [record.getMessage(), record.exc_text or "", *(repr(v) for v in vars(record).values())]
    )


@pytest.mark.anyio
@pytest.mark.xfail(strict=True, reason=SPEC)
async def test_logs_carry_typed_facts_never_the_state_or_questions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FIX-jev-mcp-09: each call logs one `jev.ask` line on the `jev_mcp` logger with typed
    facts only (endpoint host, models, question count, input tokens, latency, outcome), on
    success and on failure; no log line anywhere carries the state or a question's text."""
    caplog.set_level(logging.DEBUG)
    calls: Iterator[Callable[[httpx2.Request], httpx2.Response]] = iter(
        [_answered, lambda r: httpx2.Response(503, json={})]
    )

    def first_answers_then_fails(request: httpx2.Request) -> httpx2.Response:
        return next(calls)(request)

    fake = FakeEndpoint(first_answers_then_fails)
    async with Client(_server(transport=fake)) as client:
        await client.call_tool("ask", _args())
        await client.call_tool("ask", _args())

    lines = [r for r in caplog.records if r.name == "jev_mcp" and r.getMessage() == "jev.ask"]
    assert len(lines) == 2
    ok, failed = lines
    assert ok.__dict__["outcome"] == "ok"
    assert ok.__dict__["endpoint"] == "api.typesafe.ai"
    assert ok.__dict__["model_requested"] == PINNED
    assert ok.__dict__["model_answered"] == PINNED
    assert ok.__dict__["questions"] == 2
    assert ok.__dict__["input_tokens"] == 161
    assert isinstance(ok.__dict__["latency_ms"], int)
    assert failed.__dict__["outcome"] == "provider_unavailable"
    for record in caplog.records:
        assert STATE_CANARY not in _all_text(record), record.name
        assert QUESTION_CANARY not in _all_text(record), record.name


def _run_server(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """The server as Hermes starts it, with stdin closed so a server that does start
    reads end-of-input and stops."""
    return subprocess.run(
        [sys.executable, "-m", "jev_mcp.server"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _clean_env(**values: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SERVER_ENV}
    return {**{k: v for k, v in env.items() if not k.startswith("TYPESAFE_")}, **values}


@pytest.mark.parametrize(
    ("env", "named", "secret"),
    [
        pytest.param({}, "TYPESAFE_API_KEY", None, id="typesafe-key-missing"),
        pytest.param(
            {"TYPESAFE_API_KEY": "not a key"}, "TYPESAFE_API_KEY", "not a key", id="key-invalid"
        ),
        pytest.param(
            {"TYPESAFE_API_KEY": "tsk-test-0000", "TYPESAFE_DEFAULT_MODEL": "jev-latest"},
            "TYPESAFE_DEFAULT_MODEL",
            "tsk-test-0000",
            id="typesafe-model-unpinned",
        ),
        pytest.param(
            {"TYPESAFE_API_KEY": "tsk-test-0000", "TYPESAFE_BASE_URL": "http://1.1.1.1:8000"},
            "TYPESAFE_BASE_URL",
            "tsk-test-0000",
            id="key-over-cleartext-to-a-public-host",
        ),
        pytest.param(
            {"TYPESAFE_BASE_URL": LOCAL_BASE, "SYSTEMONE_MAX_REQUEST_TOKENS": "lots"},
            "SYSTEMONE_MAX_REQUEST_TOKENS",
            None,
            id="limit-not-a-number",
        ),
    ],
)
@pytest.mark.xfail(strict=True, reason=SPEC)
def test_an_unusable_configuration_stops_the_server_at_startup(
    env: dict[str, str], named: str, secret: str | None
) -> None:
    """FIX-jev-mcp-10: a configuration the server cannot use stops it at startup, non-zero,
    naming the variable at fault and never a key's value: no usable key for TypeSafe's
    API, an unpinned model there, a key bound for a public host over plain http (CWE-319),
    a limit that is not a positive whole number. Today it starts, and every call fails
    with an exception the agent sees only as `Error executing tool ask`."""
    run = _run_server(_clean_env(**env))
    assert run.returncode != 0
    assert named in run.stderr
    if secret is not None:
        assert secret not in run.stderr + run.stdout


def test_a_local_endpoint_without_a_key_starts() -> None:
    """FIX-jev-mcp-11 (holds today; kept through the fix): a System One endpoint on a
    private address needs no key, so the server starts without one (and, its stdin
    closed, stops cleanly)."""
    run = _run_server(_clean_env(TYPESAFE_BASE_URL=LOCAL_BASE, TYPESAFE_DEFAULT_MODEL="clef"))
    assert run.returncode == 0, run.stderr


def test_sdk_body_logging_stays_off_when_asked_for() -> None:
    """FIX-jev-mcp-12 (holds today; kept through the fix): the SDK logs request and
    response bodies at DEBUG, unredacted, and `TYPESAFE_LOG_LEVEL=debug` turns that on at
    import. Importing the server keeps the SDK's logger at INFO or above."""
    env = _clean_env(TYPESAFE_LOG_LEVEL="debug")
    probe = (
        "import logging, jev_mcp.server; "
        "print(logging.getLogger('typesafe_sdk').getEffectiveLevel())"
    )
    run = subprocess.run(  # noqa: S603  # our own interpreter and a fixed probe
        [sys.executable, "-c", probe], env=env, capture_output=True, text=True, check=True
    )
    assert int(run.stdout.strip()) >= logging.INFO


def _constant(path: Path, name: str) -> str:
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue
        if name in targets and isinstance(node.value, ast.Constant):
            return str(node.value.value)
    raise AssertionError(f"{name} not found in {path}")


@pytest.mark.req("FR-11.2")
def test_the_server_pins_the_model_tumnis_pins() -> None:
    """FIX-jev-mcp-13 (holds today; guards drift): the server's default model is the one
    Tumnis pins for its own decisions and the one the recordings are made with. A model
    bump moves all three together."""
    assert {
        _constant(REPO / "profiles/shared/jev-mcp/jev_mcp/server.py", "PINNED_MODEL"),
        _constant(REPO / "backend/tumnis/modules/decisions/api.py", "PINNED_JEV_DEFAULT"),
        _constant(REPO / "scripts/record_jev.py", "PINNED_MODEL"),
    } == {PINNED}
