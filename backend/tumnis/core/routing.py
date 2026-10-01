"""Every /v1 route is declared through one router class (P0-10, SAAS-1, REL-2, PERF-1).

`v1_router(module)` builds an `APIRouter` whose routes are `TumnisRoute`s; `create_app`
includes each under `/v1`. A `TumnisRoute` refuses to exist without a `RoutePolicy` (set
with `@route_policy(...)` under the route decorator), stores the policy on
`request.state.policy`, applies the rate limit and runs idempotent writes through
`tumnis.core.idempotency.run`:

    router = v1_router("tasks", prefixed=True)

    @router.post("", status_code=201)
    @route_policy(RoutePolicy(auth="session_or_key", scopes=frozenset({"tasks:write"}),
                              idempotent=True))
    async def create_task(body: TaskIn, session: SessionDep) -> TaskOut: ...

`walk_routes(app)` lists every route with nested included routers unpacked (FastAPI 0.141
keeps an included router lazy, so `app.routes` alone does not show its routes);
`route_violations(app)` is the registry the meta-test (tests/meta/test_route_registry.py)
and the A0.3 sweep build on.

Before the handler (and before idempotency) `TumnisRoute` refuses a write from another
origin (403 `bad_origin`), a route that needs a principal without one (401, with the
authentication middleware's reason, P0-13) and a session write without the session's
CSRF token (403 `csrf_failed`). Then `authorize` (P0-14, R-28): an API key or token on a
session-only route is 403 `session_required`; a key or token without every scope the
policy names is 403 `insufficient_scope` (sessions hold every scope); a project-limited
key or task token aiming at another project is 404 `not_found`, the answer RLS gives for
another workspace's row, so existence never leaks. The project comes from the policy's
`project_param`: `path:<name>`, `query:<name>`, `body:<name>` or `lookup:<module>` (the
module registers `register_project_lookup(module, fn)`, which resolves the route's row id
to its project in the caller's workspace; a row that belongs to no project but that every
caller of the workspace may read, such as a workspace knowledge-base document, resolves to
`WORKSPACE_ROW`). Last, a NUL character (U+0000) in a path or query
parameter or anywhere in a JSON body is 422 `validation_error`: PostgreSQL text cannot hold
it, and a handler passing it on would answer 500.
"""

import hmac
import inspect
import json
import weakref
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Sequence
from dataclasses import dataclass, field
from math import ceil
from typing import Any, Final, Literal, TypeVar, get_args, get_origin
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import StreamingResponse

from tumnis.core import idempotency, modules
from tumnis.core.errors import Problem, ProblemError
from tumnis.core.pagination import Page
from tumnis.core.principal import Principal, principal_of, unauthenticated
from tumnis.core.ratelimit import RateLimiter
from tumnis.core.tenancy import WorkspaceContext

AuthMode = Literal[
    "none", "session", "session_or_key", "key_or_task_token", "bearer_metrics", "device_token"
]
AUTH_MODES: Final = frozenset(get_args(AuthMode))
WRITE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
REQUEST_LOG_SIZE: Final = 500  # plan default: GET /v1/test/requests keeps the last 500 writes
F = TypeVar("F", bound=Callable[..., Any])

# Every /v1 route documents the problem answers it can give (P0-11's fuzzing checks them).
PROBLEM_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status: {"model": Problem, "description": description}
    for status, description in (
        (400, "Bad request (`idempotency_key_required`, `invalid_cursor`, ...)"),
        (401, "Unauthenticated (`unauthenticated`, `session_expired`)"),
        (
            403,
            "Forbidden (`csrf_failed`, `bad_origin`, `session_required`, `insufficient_scope`,"
            " ...)",
        ),
        (404, "Not found"),
        (409, "Conflict (`stale_version` with `current`)"),
        (413, "Body too large"),
        (422, "Validation error or `idempotency_mismatch`"),
        (429, "Rate limited (`Retry-After`)"),
    )
}


@dataclass(frozen=True)
class RoutePolicy:
    auth: AuthMode
    scopes: frozenset[str] = frozenset()  # checked in P0-14; session callers skip scope checks
    idempotent: bool | None = None  # writes must say True, or False with not_idempotent_reason
    not_idempotent_reason: str | None = None
    paginated: bool = False  # required when the response model is Page[...]
    unpaginated_reason: str | None = None  # a bare list only with a reason (bounded by input)
    project_param: str | None = None  # path:, query:, body:<name> or lookup:<module> (P0-14)
    csrf: bool = True  # session writes need CSRF (P0-13); False only with a reason
    csrf_exempt_reason: str | None = None  # why csrf=False (printed by the CSRF sweep)
    rate_limit: str = "default"  # bucket name in ratelimit.BUCKETS
    max_body_bytes: int = 1_048_576  # plan default 1 MiB; uploads override (P1-16)
    redact_on_replay: tuple[str, ...] = field(default=())  # never stored for replay (P0-14)


POLICY_ATTR: Final = "__tumnis_policy__"


def route_policy(policy: RoutePolicy) -> Callable[[F], F]:
    """Stores the policy on the endpoint as __tumnis_policy__. Put it under the route
    decorator, so the policy is there when the route is built."""

    def mark(endpoint: F) -> F:
        setattr(endpoint, POLICY_ATTR, policy)
        return endpoint

    return mark


class RouteWithoutPolicy(TypeError):  # noqa: N818  # the plan's name
    def __init__(self, path: str) -> None:
        super().__init__(f"route {path} has no RoutePolicy; add @route_policy(...)")
        self.path = path


def _template(request: Request, route: APIRoute) -> str:
    """The full path template (`/v1/demo-items/{item_id}`). FastAPI 0.141 resolves an
    included route through an effective context carrying the prefixed path; fall back to
    the route's own path for a route mounted directly."""
    context = request.scope.get("fastapi", {}).get("effective_route_context")
    if context is not None and getattr(context, "original_route", None) is route:
        return str(context.path)
    return route.path


_CONCRETE: weakref.WeakKeyDictionary[Any, tuple[int, dict[str, frozenset[str]]]] = (
    weakref.WeakKeyDictionary()
)


def _concrete_methods(app: Any) -> dict[str, frozenset[str]]:
    """Path -> methods for every route path without a parameter, cached per app (built
    again when routes are added)."""
    count = len(getattr(app, "routes", ()))
    cached = _CONCRETE.get(app)
    if cached is None or cached[0] != count:
        found: dict[str, set[str]] = {}
        for context in walk_routes(app):
            if context.path and context.methods and "{" not in context.path:
                found.setdefault(context.path, set()).update(context.methods)
        cached = (count, {path: frozenset(methods) for path, methods in found.items()})
        _CONCRETE[app] = cached
    return cached[1]


def _check_concrete_path(request: Request, template: str) -> None:
    """A concrete path is matched before a templated one (OpenAPI's Paths Object), so a
    templated route does not serve a path a concrete route owns: a method that concrete
    route lacks answers 405 (`GET /v1/plan/replan`, which `POST /v1/plan/replan` owns, is
    not `GET /v1/plan/{day}` with `day="replan"`; found by the P0-11 fuzzer). The `Allow`
    header comes from `errors._allowed_methods`, which applies the same rule."""
    if "{" not in template:
        return
    methods = _concrete_methods(request.app).get(request.url.path)
    if methods is not None and request.method not in methods:
        raise StarletteHTTPException(405)


def _check_rate(request: Request, policy: RoutePolicy) -> None:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if not isinstance(limiter, RateLimiter):
        return
    principal = principal_of(request)
    if principal.anonymous:
        bucket = "anonymous" if policy.rate_limit == "default" else policy.rate_limit
        client = request.client
        subject = f"address:{client.host if client else 'unknown'}"
    else:
        bucket, subject = policy.rate_limit, f"principal:{principal.key}"
    wait = limiter.check(bucket, subject)
    if wait is not None:
        raise ProblemError(
            429,
            "rate_limited",
            "Too many requests; retry after the time in Retry-After",
            headers={"Retry-After": str(max(1, ceil(wait)))},
        )


PRINCIPAL_AUTH: Final = frozenset({"session", "session_or_key", "key_or_task_token"})
CSRF_HEADER: Final = "X-CSRF-Token"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _check_origin(request: Request) -> None:
    """A browser write whose Origin is not this app's (PUBLIC_BASE_URL, else the
    request's own origin) is 403 `bad_origin` (P0-13, SEC-1). Clients that send no Origin
    (tools, API keys) pass; SameSite cookies and the CSRF token still apply."""
    given = request.headers.get("origin")
    if given is None:
        return
    base = getattr(getattr(request.app.state, "settings", None), "public_base_url", None)
    allowed = _origin(base) if base else _origin(str(request.base_url))
    if given.lower().rstrip("/") != allowed:
        raise ProblemError(403, "bad_origin", "This request came from another site")


def _check_auth(request: Request, policy: RoutePolicy) -> None:
    """401 before the handler (and before idempotency) when a route that needs a
    principal has none; the code says why (`unauthenticated`, `session_expired`). A
    session write also needs `X-CSRF-Token` equal to the session's token (P0-13): 403
    `csrf_failed` otherwise. Scope checks join here with P0-14."""
    if policy.auth not in PRINCIPAL_AUTH:
        return
    principal = principal_of(request)
    if principal.anonymous:
        raise unauthenticated(request)
    if request.method in WRITE_METHODS and principal.kind == "session" and policy.csrf:
        given = request.headers.get(CSRF_HEADER, "")
        expected = principal.csrf_token or ""
        if not (given and expected and hmac.compare_digest(given.encode(), expected.encode())):
            raise ProblemError(403, "csrf_failed", "Send the X-CSRF-Token of this session")


# --- Authorization (P0-14, SEC-2, FR-14.10, R-28) --------------------------------------------

# Which principal kinds each auth mode lets in; `authorize` answers 403 for the others.
ALLOWED_KINDS: Final[dict[str, frozenset[str]]] = {
    "session": frozenset({"session"}),
    "session_or_key": frozenset({"session", "api_key", "task_token"}),
    "key_or_task_token": frozenset({"api_key", "task_token"}),
}
PROJECT_SOURCES: Final = frozenset({"path", "query", "body", "lookup"})

ProjectLookup = Callable[[WorkspaceContext, UUID], Awaitable[UUID | None]]
# What a lookup answers for a row of the workspace that belongs to no project and that a
# project-limited caller may still read (P2-17: a workspace knowledge-base document).
WORKSPACE_ROW: Final = UUID(int=0)
_project_lookups: dict[str, ProjectLookup] = {}


def register_project_lookup(module: str, lookup: ProjectLookup) -> None:
    """`lookup:<module>` routes resolve their row id to its project with `lookup(ctx, id)`
    (the module's `api.project_of`), run in the caller's workspace."""
    _project_lookups[module] = lookup


def project_lookup(module: str) -> ProjectLookup | None:
    """The lookup `module` registered, if any (the agent surface locates a write's project
    or row with it, P2-01)."""
    return _project_lookups.get(module)


def _not_found() -> ProblemError:
    return ProblemError(404, "not_found", "Not found")


def authorize(principal: Principal, policy: RoutePolicy, project_id: UUID | None) -> None:
    """After the 401 check: 403 `session_required` (a session-only route and not a
    session); 403 `insufficient_scope` (a key or token without every scope the policy
    names; sessions hold every scope); 404 `not_found` (a project-limited key or task
    token aiming at a project outside its limit, R-28)."""
    allowed = ALLOWED_KINDS.get(policy.auth)
    if allowed is not None and principal.kind not in allowed:
        if policy.auth == "session":
            raise ProblemError(403, "session_required", "Only a signed-in session can do this")
        if principal.kind == "session":
            raise ProblemError(403, "key_required", "Only an API key or a task token can do this")
        raise ProblemError(403, "insufficient_scope", "This credential cannot call this route")
    if principal.kind != "session" and not policy.scopes <= principal.scopes:
        missing = ", ".join(sorted(policy.scopes - principal.scopes))
        raise ProblemError(403, "insufficient_scope", f"This key lacks the scope: {missing}")
    limit = principal.project_ids
    if project_id is not None and limit is not None and project_id not in limit:
        raise _not_found()


def _uuid(raw: Any) -> UUID | None:
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw)) if raw is not None else None
    except ValueError:
        return None  # not an id: the route's own validation answers


async def _body_field(request: Request, name: str) -> Any:
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return None
    return body.get(name) if isinstance(body, dict) else None


async def project_of_request(
    request: Request, policy: RoutePolicy, principal: Principal
) -> UUID | None:
    """The project a request names through the policy's `project_param`; only looked up
    for a project-limited principal (the others may touch every project). A `lookup:` row
    that resolves to no project is 404 for such a principal, as its handler would say."""
    if not policy.project_param or principal.project_ids is None:
        return None
    source, _, name = policy.project_param.partition(":")
    if source == "path":
        return _uuid(request.path_params.get(name))
    if source == "query":
        return _uuid(request.query_params.get(name))
    if source == "body":
        return _uuid(await _body_field(request, name))
    lookup = _project_lookups.get(name)
    if lookup is None:
        raise RuntimeError(f"no project lookup is registered for {policy.project_param}")
    params = request.path_params
    row_id = _uuid(params.get("id", next(iter(params.values()), None)))
    if row_id is None:
        return None
    project = await lookup(principal.workspace_context(), row_id)
    if project is None:
        raise _not_found()
    return None if project == WORKSPACE_ROW else project


async def _authorize(request: Request, policy: RoutePolicy) -> None:
    if policy.auth not in PRINCIPAL_AUTH:
        return
    principal = principal_of(request)
    authorize(principal, policy, None)
    project_id = await project_of_request(request, policy, principal)
    if project_id is not None:
        authorize(principal, policy, project_id)


# --- NUL in request strings ------------------------------------------------------------------
#
# PostgreSQL text cannot hold the character U+0000 (psycopg refuses to send one: DataError),
# and no module's own string types forbid it, so a route that passed such a string to the
# database answered 500 (found by the P0-11 fuzzer on POST /v1/auth/login). TumnisRoute
# refuses it for every /v1 route, as the validation error any other malformed input gets.

NUL: Final = "\x00"
NUL_DETAIL: Final = "text must not contain the NUL character (U+0000)"


def _json_media(content_type: str | None) -> bool:
    """The bodies FastAPI parses as JSON: `application/json` and `application/*+json`
    (a body without a content type is refused by FastAPI's strict content type)."""
    if not content_type:
        return False
    main, _, sub = content_type.split(";", 1)[0].strip().lower().partition("/")
    return main == "application" and (sub == "json" or sub.endswith("+json"))


NUL_WHERE_MAX: Final = 200  # a location longer than this is cut short in the detail

# A location while walking: (parent location, key or index), None at the body's root. Each
# child holds a reference to its parent, so a long key over a long array costs one small
# tuple per element, not a copy of the whole path; the text is built only for a hit.
_Where = tuple[Any, str | int] | None


def _location(where: _Where) -> str:
    parts: list[str] = []
    while where is not None:
        where, part = where
        parts.append(str(part))
    return ".".join(["body", *reversed(parts)])


def _nul_in_json(document: Any) -> str | None:
    """Where the first string (a value or an object key) holding a NUL sits, as
    `body.a.0.b`; walked with a stack, so a deep document cannot exhaust the recursion."""
    stack: list[tuple[Any, _Where]] = [(document, None)]
    while stack:
        item, where = stack.pop()
        if isinstance(item, str):
            if NUL in item:
                return _location(where)
        elif isinstance(item, dict):
            for key, child in item.items():
                if NUL in key:
                    return _location(where)
                stack.append((child, (where, key)))
        elif isinstance(item, list):
            stack.extend((child, (where, index)) for index, child in enumerate(item))
    return None


def _nul_in_params(request: Request) -> str | None:
    for name, value in request.path_params.items():
        if isinstance(value, str) and NUL in value:
            return f"path.{name}"
    for name, value in request.query_params.multi_items():
        if NUL in name:
            return "query"
        if NUL in value:
            return f"query.{name}"
    return None


async def _nul_in_body(request: Request) -> str | None:
    if not _json_media(request.headers.get("content-type")):
        return None
    body = await request.body()
    # A NUL reaches a JSON string only as the escape \u0000, or as a zero byte of a UTF-16
    # or UTF-32 body (json.loads reads those too); without either there is nothing to parse.
    if b"\\u0000" not in body and b"\x00" not in body:
        return None
    try:
        document = await request.json()  # Starlette caches the parse; FastAPI reuses it
    except (ValueError, RecursionError):
        return None  # not JSON: FastAPI answers its own 422 `validation_error`
    return _nul_in_json(document)


async def _check_nul(request: Request) -> None:
    """422 `validation_error` for a NUL in a path or query parameter or anywhere in a JSON
    body. It runs after the auth checks and before idempotency, so nothing is stored for
    the key, and it never depends on what exists (a known and an unknown email get the
    same answer)."""
    where = _nul_in_params(request) or await _nul_in_body(request)
    if where is not None:
        if len(where) > NUL_WHERE_MAX:  # names come from the caller: never echo a long one
            where = where[: NUL_WHERE_MAX - 3] + "..."
        raise ProblemError(422, "validation_error", f"{where}: {NUL_DETAIL}")


def _log_write(request: Request, template: str, response: Response) -> None:
    log = getattr(request.app.state, "request_log", None)
    if isinstance(log, deque):
        log.append(
            {
                "method": request.method,
                "path": request.url.path,
                "route": template,
                "idempotency_key": request.headers.get(idempotency.KEY_HEADER),
                "status": response.status_code,
                "replayed": response.headers.get(idempotency.REPLAYED_HEADER) == "true",
            }
        )


class TumnisRoute(APIRoute):
    """Refuses to build a route without a policy; wraps writes marked idempotent."""

    @property
    def policy(self) -> RoutePolicy:
        policy: RoutePolicy = getattr(self.endpoint, POLICY_ATTR)
        return policy

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        policy = getattr(self.endpoint, POLICY_ATTR, None)
        if not isinstance(policy, RoutePolicy):
            raise RouteWithoutPolicy(self.path)
        original = super().get_route_handler()
        route = self

        async def handler(request: Request) -> Response:
            request.state.policy = policy
            template = _template(request, route)
            request.state.route_template = template
            _check_concrete_path(request, template)
            if request.method in WRITE_METHODS:
                _check_origin(request)
            _check_rate(request, policy)
            _check_auth(request, policy)
            await _authorize(request, policy)
            await _check_nul(request)
            if request.method not in WRITE_METHODS:
                return await original(request)
            if policy.idempotent:
                response = await idempotency.run(request, original)
            else:
                response = await original(request)
            _log_write(request, template, response)
            return response

        return handler


def v1_router(module: str, **kw: Any) -> APIRouter:
    """A router whose routes are TumnisRoutes, included by create_app under /v1.
    `prefixed=True` puts it under `/<module>`; `prefix=` sets another. A registered module
    gets its on/off flag (404 while switched off, P0-08)."""
    prefixed = kw.pop("prefixed", False)
    prefix = kw.pop("prefix", f"/{module}" if prefixed else "")
    dependencies = list(kw.pop("dependencies", ()))
    if module in modules.MODULES:
        dependencies.insert(0, Depends(modules.require_module(module)))
    responses = {**PROBLEM_RESPONSES, **kw.pop("responses", {})}
    return APIRouter(
        prefix=prefix,
        route_class=TumnisRoute,
        dependencies=dependencies,
        responses=responses,
        **kw,
    )


def new_request_log() -> deque[dict[str, Any]]:
    return deque(maxlen=REQUEST_LOG_SIZE)


# --- The registry ---------------------------------------------------------------------------


def walk_routes(app: Any) -> list[RouteContext]:
    """Every route of the app with nested included routers unpacked; each item has the
    full `path`, `methods`, `endpoint`, `response_model`, `dependant` and `original_route`
    (the route object the router built)."""
    routes: Iterable[Any] = getattr(app, "routes", ())
    return list(iter_route_contexts(list(routes)))


def policy_of(route: Any) -> RoutePolicy | None:
    policy = getattr(getattr(route, "endpoint", None), POLICY_ATTR, None)
    return policy if isinstance(policy, RoutePolicy) else None


def _is_page(model: Any) -> bool:
    return inspect.isclass(model) and issubclass(model, Page)


def _is_bare_collection(model: Any) -> bool:
    origin = get_origin(model)
    return origin in (list, tuple, set, frozenset) or (
        inspect.isclass(origin) and issubclass(origin, Sequence) and origin is not str
    )


def _query_names(route: RouteContext) -> set[str]:
    """Query parameter names of the endpoint and every dependency under it."""
    names: set[str] = set()
    stack: list[Dependant] = [route.dependant]
    while stack:
        dependant = stack.pop()
        names.update(param.alias or param.name for param in dependant.query_params)
        stack.extend(dependant.dependencies)
    return names


def _policy_violations(label: str, route: RouteContext, policy: RoutePolicy) -> list[str]:
    out = []
    if policy.auth not in AUTH_MODES:
        out.append(f"{label}: auth mode {policy.auth!r} is not one of {sorted(AUTH_MODES)}")
    if policy.project_param is not None:
        source, _, name = policy.project_param.partition(":")
        if source not in PROJECT_SOURCES or not name:
            out.append(
                f"{label}: project_param {policy.project_param!r} is not path:, query:,"
                " body:<name> or lookup:<module>"
            )
    if set(route.methods or ()) & WRITE_METHODS:
        if policy.idempotent is None or (
            policy.idempotent is False and not policy.not_idempotent_reason
        ):
            out.append(
                f"{label}: a write must be idempotent=True, or idempotent=False with a"
                " not_idempotent_reason"
            )
        response_class = getattr(route, "response_class", None)
        if (
            policy.idempotent
            and inspect.isclass(response_class)
            and issubclass(response_class, StreamingResponse)
        ):
            out.append(f"{label}: an idempotent write cannot stream its response")
    model = route.response_model
    if _is_bare_collection(model) and not policy.unpaginated_reason:
        out.append(f"{label}: returns a bare list; return Page[...] with paginated=True")
    elif _is_page(model):
        if not policy.paginated:
            out.append(f"{label}: returns a Page but the policy is not paginated=True")
        missing = {"cursor", "limit"} - _query_names(route)
        if missing:
            out.append(f"{label}: a paginated route must accept {sorted(missing)}")
    elif policy.paginated:
        out.append(f"{label}: paginated=True but the response model is not a Page[...]")
    return out


def route_violations(app: Any) -> list[str]:
    """Every way a /v1 route breaks the conventions, as "<METHODS> <path>: why"."""
    out: list[str] = []
    openapi_url = getattr(app, "openapi_url", None)
    for route in walk_routes(app):
        path = route.path or ""
        if not path.startswith("/v1/") or path == openapi_url:
            continue
        original = route.original_route
        label = f"{','.join(sorted(route.methods or ())) or 'ANY'} {path}"
        if not isinstance(original, TumnisRoute):
            kind = "a plain APIRoute" if isinstance(original, APIRoute) else type(original).__name__
            out.append(f"{label}: {kind}, not a TumnisRoute (declare it on v1_router)")
            continue
        policy = policy_of(route)
        if policy is None:
            out.append(f"{label}: no RoutePolicy")
            continue
        out.extend(_policy_violations(label, route, policy))
    return out
