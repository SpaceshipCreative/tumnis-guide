"""`/mcp`: the op registry served as MCP tools over Streamable HTTP (P2-01, FR-14.10).

Built on the official MCP Python SDK as pinned (`mcp==1.30.0`, its low-level `Server`, the
plan's documented fallback): each op is listed with an explicit `inputSchema` and
`outputSchema` from its pydantic models, so the tool schema is exactly the REST twin's
(the parity meta-test compares them), and tool calls skip the SDK's own argument check so
`invoke` answers every refusal with the same problem code as the twin. A refused call is a
tool error result (`isError: true`) whose `structuredContent` is the problem object and
whose text is the same JSON.

Transport: stateless, one JSON body per answer (`stateless=True, json_response=True`): no
session to pin to one replica, and the stdio shim stays a line forwarder. Each app gets its
own `StreamableHTTPSessionManager` (`app.state.mcp_session_manager`, single use: the
lifespan runs it once), mounted as a raw `Route("/mcp")` so `POST /mcp` is never
redirected to `/mcp/`.

`MCPEndpoint` guards the route before the SDK sees a byte: a browser `Origin` other than
the app's is 403 `bad_origin` (the spec's DNS-rebinding rule); a session cookie is 401
`mcp_requires_bearer` (so CSRF cannot reach a tool); no credential is 401; the per-key
rate limit (P0-10) applies; then it puts the `Caller` on the request state, where the
tool handler reads it.
"""

import json
import logging
from datetime import datetime
from math import ceil
from typing import Any, Final

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from tumnis.core import agent_surface
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.errors import Problem, ProblemError, problem, problem_response
from tumnis.core.principal import Principal, principal_of, unauthenticated
from tumnis.core.ratelimit import RateLimiter
from tumnis.core.schemas import UnsupportedSchemaVersion
from tumnis.core.versioning import NotFound, StaleVersion

SERVER_NAME: Final = "tumnis"
MCP_PATH: Final = "/mcp"
CALLER_STATE: Final = "caller"
_log = logging.getLogger(__name__)


# --- Tools -----------------------------------------------------------------------------------


def input_schema(op: agent_surface.SurfaceOp) -> dict[str, Any]:
    return op.input_model.model_json_schema(mode="validation")


def output_schema(op: agent_surface.SurfaceOp) -> dict[str, Any]:
    return op.output_model.model_json_schema(mode="serialization")


def tool_of(op: agent_surface.SurfaceOp) -> types.Tool:
    return types.Tool(
        name=op.name,
        description=op.description,
        inputSchema=input_schema(op),
        outputSchema=output_schema(op),
    )


def catalogue() -> list[dict[str, Any]]:
    """The tool catalogue (`schemas/mcp/v1/tools.json`, P2-12's mock server input)."""
    return [
        {
            "name": op.name,
            "description": op.description,
            "scope": op.scope,
            "write": op.write,
            "updates_existing": op.updates_existing,
            "master_only": op.master_only,
            "schema_version": op.schema_version,
            "rest": {"method": op.rest_method, "path": op.rest_path},
            "input_schema": input_schema(op),
            "output_schema": output_schema(op),
        }
        for op in agent_surface.ops()
        if not op.name.startswith("_")
    ]


def catalogue_json() -> str:
    """The catalogue as committed: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(catalogue(), indent=2, sort_keys=True) + "\n"


def problem_of(exc: Exception) -> Problem:
    """The problem a raised error answers, as the REST handlers would map it."""
    if isinstance(exc, ProblemError):
        return exc.problem
    if isinstance(exc, StaleVersion):
        return problem(
            409,
            "stale_version",
            "The row changed since you read it; `current` holds it now",
            current=exc.current,
        )
    if isinstance(exc, NotFound):
        return problem(404, "not_found", "Not found")
    if isinstance(exc, UnsupportedSchemaVersion):
        return problem(422, exc.code, str(exc))
    if isinstance(exc, ValidationError):
        return agent_surface.validation_problem(exc).problem
    _log.exception("mcp tool call failed")
    return problem(500, "internal_error")


def error_result(found: Problem) -> types.CallToolResult:
    body = found.model_dump(mode="json", exclude_none=True)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(body))],
        structuredContent=body,
        isError=True,
    )


def ok_result(out: BaseModel) -> types.CallToolResult:
    body = out.model_dump(mode="json")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(body))],
        structuredContent=body,
        isError=False,
    )


def _now(request: Request) -> datetime:
    clock: Clock = getattr(request.app.state, "clock", None) or SystemClock()
    return clock.now()


async def call_tool(request: Request, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """One tool call for the caller `MCPEndpoint` put on the request."""
    caller = getattr(request.state, CALLER_STATE, None)
    if not isinstance(caller, agent_surface.Caller):  # pragma: no cover  # the endpoint sets it
        return error_result(problem(401, "unauthenticated", "Send an API key or a task token"))
    try:
        op = agent_surface.get_op(name)
        out = await agent_surface.invoke(op, caller, arguments, door="mcp", now=_now(request))
    except Exception as exc:  # every failure is answered as a problem
        return error_result(problem_of(exc))
    return ok_result(out)


def build_server() -> Server[Any, Request]:
    """The low-level server listing the registry; the registry is read per request, so an
    op registered later (or a test-only one) is listed without a rebuild."""
    server: Server[Any, Request] = Server(SERVER_NAME)

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [tool_of(op) for op in agent_surface.ops()]

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def handle_call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        request = server.request_context.request
        if request is None:  # pragma: no cover  # the HTTP transport always sets it
            return error_result(problem(400, "bad_request", "No HTTP request"))
        return await call_tool(request, name, arguments)

    return server


SERVER: Final = build_server()


def session_manager() -> StreamableHTTPSessionManager:
    """A fresh manager for one app (single use: `run()` once, in the lifespan)."""
    return StreamableHTTPSessionManager(app=SERVER, json_response=True, stateless=True)


# --- The endpoint ----------------------------------------------------------------------------


def _origin(url: str) -> str:
    from urllib.parse import urlsplit  # noqa: PLC0415

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _same_origin(request: Request) -> bool:
    """No Origin (an agent, a script) or this app's own (PUBLIC_BASE_URL, else the
    request's): the transport spec's DNS-rebinding rule, as TumnisRoute applies it."""
    given = request.headers.get("origin")
    if given is None:
        return True
    base = getattr(getattr(request.app.state, "settings", None), "public_base_url", None)
    allowed = _origin(base) if base else _origin(str(request.base_url))
    return given.lower().rstrip("/") == allowed


class MCPEndpoint:
    """The ASGI app behind `Route("/mcp")`: origin, bearer, rate limit, then the SDK."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        refused = self._refusal(request)
        if refused is not None:
            await problem_response(refused.problem, refused.headers)(scope, receive, send)
            return
        caller = await agent_surface.resolve_caller(principal_of(request))
        scope.setdefault("state", {})[CALLER_STATE] = caller
        manager: StreamableHTTPSessionManager = request.app.state.mcp_session_manager
        await manager.handle_request(scope, receive, send)

    def _refusal(self, request: Request) -> ProblemError | None:
        if not _same_origin(request):
            return ProblemError(403, "bad_origin", "This request came from another site")
        principal = principal_of(request)
        refused = _credential_refusal(request, principal)
        if refused is not None:
            return refused
        limiter = getattr(request.app.state, "rate_limiter", None)
        wait = (
            limiter.check("default", f"principal:{principal.key}")
            if isinstance(limiter, RateLimiter)
            else None
        )
        if wait is None:
            return None
        return ProblemError(
            429,
            "rate_limited",
            "Too many requests; retry after the time in Retry-After",
            headers={"Retry-After": str(max(1, ceil(wait)))},
        )


def _credential_refusal(request: Request, principal: Principal) -> ProblemError | None:
    """A session (or only cookies) is 401 `mcp_requires_bearer`; nothing is 401 with the
    authentication middleware's reason; a device token is 403."""
    bearer = "authorization" in request.headers
    if principal.kind == "session" or (principal.anonymous and not bearer and request.cookies):
        return ProblemError(
            401, "mcp_requires_bearer", "MCP takes an API key or a task token, not a session"
        )
    if principal.anonymous:
        return unauthenticated(request)
    if principal.kind not in ("api_key", "task_token"):
        return ProblemError(403, "insufficient_scope", "This credential cannot call tools")
    return None


def mcp_route() -> Route:
    """`/mcp` as a raw route (not a Mount, which would redirect `POST /mcp`)."""
    return Route(MCP_PATH, endpoint=MCPEndpoint(), methods=["GET", "POST", "DELETE"])
