"""The agent surface: one op registry behind both doors, MCP tools and their REST twins
(P2-01, FR-14.10, REL-2, SAAS-1, R-28, R-31, R-33, R-34).

Each module's `mcp.py` declares its ops with `register_op(SurfaceOp(...))`: a name (the MCP
tool), a scope, input and output models, the REST twin's method and path, and a handler
that calls the module's `api.py`. `tumnis.core.mcp_server` lists every op as a tool; the
module's router declares the twin, which calls the same `invoke`. The parity, scope and
write-rule meta-tests (backend/tests/meta/test_mcp_*.py) iterate the registry, so a new op
is covered without anyone remembering.

`invoke(op, caller, raw, door=...)` is the one path both doors take:

1. Scope, then project (P0-14's `authorize`, R-28): 403 `insufficient_scope`, then 404
   `not_found` for a project-limited key or task token aiming at another project, or for
   a write aimed at a project or row the workspace does not have; then 403 `master_only`
   for an op only the master key may call (checked here, never at key creation, R-34).
   The order matches the REST twin's `TumnisRoute`.
2. `schema_version`: missing or current passes; current - 1 goes through the op's
   `previous_version_adapter`; anything else is 422 `unsupported_schema_version`. It is
   judged after step 1, so a write at another workspace's row is 404 whatever it says
   (A0.3).
3. Write rules: an MCP write needs `idempotency_key` (400 `idempotency_key_required`,
   REST sends the header); the input model is validated (422 `validation_error`; an
   update without `version` fails here). MCP writes run through
   `idempotency.run_idempotent`: the same key and arguments replay the first answer, other
   arguments are 422 `idempotency_mismatch`.
4. Taint (R-31, P2-08): a write by an API key (no run) reaches the handler with
   `taint=TaintSource("keyless_write", tainted=True)`, and a write by a task token whose
   run is tainted with `taint=TaintSource("run", tainted=True)`, so what either creates
   is tainted (`caller_tainted`).
5. The handler runs inside the workspace transaction (the REST twin's own session, or one
   opened here for MCP) and answers the output model.

`Caller` adds what the principal alone does not say (the run of a task token, the
profile a key belongs to, whether it is the master's) through `register_caller_facts`
resolvers that modules register (auth: a task token's run; P2-02: the profile's key).
"""

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from tumnis.core import idempotency, routing
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.principal import Principal, principal_of
from tumnis.core.routing import RoutePolicy, authorize
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import ActorRef
from tumnis.core.versioning import Version

Scope = Literal[
    "tasks:read",
    "tasks:write",
    "context:read",
    "knowledge:write",
    "drafts:write",
    "delegate",
    "ingest",
]
Door = Literal["mcp", "rest"]
RestMethod = Literal["GET", "POST", "PATCH", "PUT", "DELETE"]
NAME_RE: Final = re.compile(r"^_?[a-z][a-z0-9_]*$")

# Tools the PRD names that later WPs bring (T-P2-01-16 checks this against the registry).
PENDING_TOOLS: Final[Mapping[str, str]] = {
    "delegate_task": "P2-06",
    "wait_for_task": "P2-06",
    "ingest_items": "P3-02",
    "get_context_item": "P3-03",
    "draft_reply": "P3-07",
}


# --- Input models (R-33) ---------------------------------------------------------------------


class SurfaceInput(BaseModel):
    """Base of every op's input and REST twin's body or query."""

    schema_version: int | None = None  # absent: the op's current version


IdempotencyKey = Annotated[str, Field(min_length=8, max_length=255, pattern=r"^[A-Za-z0-9_\-:.]+$")]


class WriteInput(SurfaceInput):
    """A write's input on MCP; the REST twin takes the key as the Idempotency-Key header."""

    idempotency_key: IdempotencyKey


class UpdateInput(WriteInput):
    """An update names the version it read (REL-2)."""

    version: Version


# --- Callers ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class CallerFacts:
    profile_id: UUID | None = None  # the agent_profiles row the key belongs to (P2-02)
    is_master: bool = False
    run_id: UUID | None = None  # a task token's run (R-31)
    run_tainted: bool = False  # that run's stored taint (P2-08); False when no row says so


@dataclass(frozen=True)
class Caller:
    principal: Principal  # P0-14's principal; authorization uses its scopes and projects
    profile_id: UUID | None = None
    is_master: bool = False
    run_id: UUID | None = None  # None for profile, cron and script keys (R-31)
    run_tainted: bool = False  # the run is tainted, so what it writes is (P2-08, SAF-1)

    @property
    def scopes(self) -> frozenset[str]:
        return self.principal.scopes

    @property
    def key_id(self) -> UUID | None:
        return self.principal.subject_id


FactsResolver = Callable[[Principal], Awaitable[CallerFacts | None]]
_facts: dict[str, FactsResolver] = {}


def register_caller_facts(name: str, resolver: FactsResolver) -> None:
    """Add (or replace) a resolver of what a principal is beyond its scopes."""
    _facts[name] = resolver


def unregister_caller_facts(name: str) -> None:
    _facts.pop(name, None)


async def resolve_caller(principal: Principal) -> Caller:
    """The caller for a principal: every registered resolver's facts, merged."""
    profile_id: UUID | None = None
    run_id: UUID | None = None
    is_master = run_tainted = False
    if not principal.anonymous:
        for resolver in list(_facts.values()):
            found = await resolver(principal)
            if found is None:
                continue
            profile_id = profile_id or found.profile_id
            run_id = run_id or found.run_id
            is_master = is_master or found.is_master
            run_tainted = run_tainted or found.run_tainted
    return Caller(
        principal,
        profile_id=profile_id,
        is_master=is_master,
        run_id=run_id,
        run_tainted=run_tainted,
    )


def caller_tainted(caller: Caller) -> bool:
    """Whether what the caller writes is tainted: a task token of a tainted run (P2-08),
    or an API key with no run (R-31). A person's session and a clean run write untainted."""
    return caller.run_tainted or (caller.principal.kind == "api_key" and caller.run_id is None)


# --- Ops -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TaintSource:
    """Why what a call writes is tainted (R-31: a write by a key with no run; P2-08: a
    write by a tainted run's token)."""

    kind: str
    tainted: bool


@dataclass(frozen=True)
class SurfaceCall:
    """What a handler gets besides its input: who calls, in which transaction, when."""

    caller: Caller
    session: AsyncSession
    now: datetime
    door: Door
    taint: TaintSource | None = None

    @property
    def actor(self) -> ActorRef:
        return self.caller.principal.actor

    @property
    def tainted(self) -> bool:
        return self.taint is not None and self.taint.tainted

    @property
    def project_ids(self) -> frozenset[UUID] | None:
        """The projects a project-limited caller may see (None: every project)."""
        return self.caller.principal.project_ids


Handler = Callable[[SurfaceCall, Any], Awaitable[BaseModel]]
AfterCommit = Callable[[Caller, BaseModel], Awaitable[BaseModel]]
ProjectResolver = Callable[[WorkspaceContext, Mapping[str, Any]], Awaitable[UUID | None]]


@dataclass(frozen=True)
class SurfaceOp:
    name: str  # MCP tool name, snake_case, unique
    description: str  # tool description; for any agent (no agent-specific terms)
    scope: Scope
    input_model: type[SurfaceInput]
    output_model: type[BaseModel]
    rest_method: RestMethod
    rest_path: str  # "/v1/tasks/{task_id}/status"
    write: bool  # needs idempotency_key; and version if it updates
    updates_existing: bool  # needs version
    project_arg: str | None  # input field naming the project, or the resolver below
    project_resolver: ProjectResolver | None  # (ctx, raw args) -> the project, or None
    handler: Handler = field(repr=False)
    master_only: bool = False
    session_twin_allowed: bool = True  # the REST twin also takes a session (the web app)
    schema_version: int = 1
    previous_version_adapter: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # Runs after the call's transaction committed (P2-05's long polls wait on the human
    # outside it): `invoke` hands it the answer, replayed or not, and answers its result.
    # Only for an op whose REST twin lets `invoke` open the transaction (no route session).
    after_commit: "AfterCommit | None" = None


class InvalidOpError(ValueError):
    """A malformed op, or one whose name or REST twin is taken."""


REGISTRY: dict[str, SurfaceOp] = {}


def _check(op: SurfaceOp) -> None:
    if not NAME_RE.match(op.name):
        raise InvalidOpError(f"op name {op.name!r} is not snake_case")
    if op.updates_existing and not op.write:
        raise InvalidOpError(f"{op.name}: an op that updates a record is a write")
    if op.write and not issubclass(op.input_model, WriteInput):
        raise InvalidOpError(f"{op.name}: a write's input subclasses WriteInput")
    if op.updates_existing and "version" not in op.input_model.model_fields:
        raise InvalidOpError(f"{op.name}: an update's input names the version it read")
    if op.project_arg is not None and op.project_arg not in op.input_model.model_fields:
        raise InvalidOpError(f"{op.name}: project_arg {op.project_arg!r} is not an input field")
    for other in REGISTRY.values():
        if other.name == op.name:
            raise InvalidOpError(f"op {op.name!r} is registered twice")
        if (other.rest_method, other.rest_path) == (op.rest_method, op.rest_path):
            raise InvalidOpError(
                f"{op.name} and {other.name} share {op.rest_method} {op.rest_path}"
            )


def register_op(op: SurfaceOp) -> SurfaceOp:
    """Adds the op; raises InvalidOpError on a duplicate name or twin, or a malformed op.
    Registering the same op object again is a no-op (a module imported twice)."""
    if REGISTRY.get(op.name) is op:
        return op
    _check(op)
    REGISTRY[op.name] = op
    return op


def unregister_op(name: str) -> None:
    """Removes an op (test-only ops, T-P2-01-11)."""
    REGISTRY.pop(name, None)


def ops() -> list[SurfaceOp]:
    """Every registered op, by name."""
    return [REGISTRY[name] for name in sorted(REGISTRY)]


def get_op(name: str) -> SurfaceOp:
    found = REGISTRY.get(name)
    if found is None:
        raise ProblemError(404, "unknown_tool", f"No tool is named {name!r}")
    return found


# --- invoke ----------------------------------------------------------------------------------


def _upgrade(op: SurfaceOp, raw: dict[str, Any]) -> dict[str, Any]:
    given = raw.get("schema_version")
    if given is None or given == op.schema_version:
        return raw
    if (
        isinstance(given, int)
        and given == op.schema_version - 1
        and op.previous_version_adapter is not None
    ):
        return op.previous_version_adapter(raw)
    raise ProblemError(
        422,
        "unsupported_schema_version",
        f"{op.name} takes schema_version {op.schema_version}"
        + (f" or {op.schema_version - 1}" if op.previous_version_adapter else ""),
    )


def _uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value)) if value is not None else None
    except ValueError:
        return None


PROJECTS_LOOKUP: Final = "projects"  # the lookup projects registers: a live project, or None


def _not_found() -> ProblemError:
    return ProblemError(404, "not_found", "Not found")


async def _project(op: SurfaceOp, caller: Caller, raw: Mapping[str, Any]) -> UUID | None:
    """The project the call names. A read looks it up only for a project-limited caller
    (the others may read every project). A write always locates its project or row, so a
    write aimed at a row the caller cannot see is 404 before anything in its body is
    judged (A0.3), as its REST twin would say."""
    principal = caller.principal
    if principal.project_ids is None and not op.write:
        return None
    ctx = principal.workspace_context()
    if op.project_arg is not None:
        project_id = _uuid(raw.get(op.project_arg))
        exists = routing.project_lookup(PROJECTS_LOOKUP)
        if project_id is not None and exists is not None and await exists(ctx, project_id) is None:
            raise _not_found()
        return project_id
    if op.project_resolver is None:
        return None
    found = await op.project_resolver(ctx, raw)
    if found is None:
        raise _not_found()
    return None if found == routing.WORKSPACE_ROW else found


def validation_problem(exc: ValidationError) -> ProblemError:
    errors = [
        f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}" for e in exc.errors()
    ]
    return ProblemError(422, "validation_error", "; ".join(errors)[:2000] or None)


def _parse(op: SurfaceOp, raw: Mapping[str, Any]) -> SurfaceInput:
    try:
        return op.input_model.model_validate(raw)
    except ValidationError as exc:
        raise validation_problem(exc) from None


def _taint(op: SurfaceOp, caller: Caller) -> TaintSource | None:
    """R-31: a write by an API key (never bound to a run) is tainted; P2-08: so is a write
    by a task token whose run is tainted."""
    if not op.write or not caller_tainted(caller):
        return None
    return TaintSource(kind="run" if caller.run_tainted else "keyless_write", tainted=True)


async def authorize_call(op: SurfaceOp, caller: Caller, raw: Mapping[str, Any]) -> None:
    """401, then 403 `insufficient_scope`, 404 `not_found` (R-28), 403 `master_only`."""
    principal = caller.principal
    if principal.anonymous:
        raise ProblemError(401, "unauthenticated", "Send an API key or a task token")
    policy = RoutePolicy(auth="session_or_key", scopes=frozenset({op.scope}))
    authorize(principal, policy, None)
    project_id = await _project(op, caller, raw)
    if project_id is not None:
        authorize(principal, policy, project_id)
    if op.master_only and not caller.is_master:
        raise ProblemError(403, "master_only", f"Only the master key can call {op.name}")


async def invoke(
    op: SurfaceOp,
    caller: Caller,
    raw: Mapping[str, Any],
    *,
    door: Door,
    now: datetime,
    session: AsyncSession | None = None,
) -> BaseModel:
    """Runs one call of `op` for `caller` (see the module docstring). The REST twin
    passes its idempotent request's session and the Idempotency-Key header as
    `idempotency_key`; MCP passes neither and gets its own transaction here."""
    # Who may call, and whether the target exists, come before the version is judged: a
    # write aimed at a row the caller cannot see is 404 whatever its schema_version says.
    try:
        data, unsupported = _upgrade(op, dict(raw)), None
    except ProblemError as exc:
        data, unsupported = dict(raw), exc
    await authorize_call(op, caller, data)
    if unsupported is not None:
        raise unsupported
    if door == "rest" and caller.principal.kind == "session" and not op.session_twin_allowed:
        raise ProblemError(403, "key_required", "Only an API key or a task token can do this")
    if op.write:
        key = data.get("idempotency_key")
        if not isinstance(key, str) or not idempotency.KEY_RE.match(key):
            raise ProblemError(
                400,
                "idempotency_key_required",
                "Send idempotency_key (8 to 255 of A-Z a-z 0-9 _ - : .) on every write",
            )
    parsed = _parse(op, data)
    taint = _taint(op, caller)

    async def run(s: AsyncSession) -> BaseModel:
        return await op.handler(SurfaceCall(caller, s, now, door, taint), parsed)

    if session is not None:
        return await run(session)
    ctx = caller.principal.workspace_context()
    if not op.write:
        async with tenant_session(ctx) as s:
            answer = await run(s)
        return answer if op.after_commit is None else await op.after_commit(caller, answer)

    async def work(s: AsyncSession) -> bytes:
        return (await run(s)).model_dump_json().encode()

    body = await idempotency.run_idempotent(
        ctx,
        principal=caller.principal.key,
        key=str(parsed.model_dump().get("idempotency_key")),
        route=f"mcp:{op.name}",
        args=parsed.model_dump(mode="json", exclude={"idempotency_key"}),
        now=now,
        work=work,
    )
    answer = op.output_model.model_validate_json(body)
    return answer if op.after_commit is None else await op.after_commit(caller, answer)


async def invoke_rest(
    op: SurfaceOp,
    caller: Caller,
    raw: Mapping[str, Any],
    *,
    session: AsyncSession,
    now: datetime,
    idempotency_key: str | None = None,
) -> BaseModel:
    """The REST twin's call: the header's key stands in for `idempotency_key`."""
    data = dict(raw)
    if op.write:
        data["idempotency_key"] = idempotency_key
    return await invoke(op, caller, data, door="rest", now=now, session=session)


async def rest_twin(
    request: Request, session: AsyncSession, op: SurfaceOp, raw: Mapping[str, Any]
) -> BaseModel:
    """What a REST twin's handler calls: the request's caller, clock and Idempotency-Key
    header, in the route's session (`TumnisRoute` already refused what its policy
    refuses; `invoke` applies the same rules again, and the ones only the op knows)."""
    clock: Clock = getattr(request.app.state, "clock", None) or SystemClock()
    return await invoke_rest(
        op,
        await resolve_caller(principal_of(request)),
        raw,
        session=session,
        now=clock.now(),
        idempotency_key=request.headers.get(idempotency.KEY_HEADER),
    )


async def rest_twin_detached(request: Request, op: SurfaceOp, raw: Mapping[str, Any]) -> BaseModel:
    """A REST twin whose route holds no session (P2-05's long polls): `invoke` opens the
    transaction (idempotent on the Idempotency-Key header, as on MCP), commits it, then
    runs the op's `after_commit`, so the wait never holds a transaction open."""
    clock: Clock = getattr(request.app.state, "clock", None) or SystemClock()
    data = dict(raw)
    if op.write:
        data["idempotency_key"] = request.headers.get(idempotency.KEY_HEADER)
    return await invoke(
        op, await resolve_caller(principal_of(request)), data, door="rest", now=clock.now()
    )
