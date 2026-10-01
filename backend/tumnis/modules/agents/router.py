"""agents REST (P1-04, FR-5.1, FR-5.9): runners and agent profiles, session only (admin).

`/v1/runners` and `/v1/agents/profiles` (not under one module prefix: the plan's paths).
Creating a runner and rotating its token answer the device token once; an idempotent
replay answers without it (`redact_on_replay`).
"""

from typing import Annotated, Final
from uuid import UUID

from fastapi import Depends, Query, Request

from tumnis.core import agent_surface as surface
from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.idempotency import SessionDep
from tumnis.core.pagination import Page, PageParams, page_params
from tumnis.core.principal import principal_of
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.agents import api
from tumnis.modules.agents import mcp as tools
from tumnis.modules.agents.packet_builder import TaskPacket

router = v1_router("agents", tags=["agents"])

Session = Annotated[WorkspaceContext, Depends(require_session)]
Paging = Annotated[PageParams, Depends(page_params)]
_TOKEN_ONCE: Final = ("token",)


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


# --- Runners -------------------------------------------------------------------------------


@router.get("/runners")
@route_policy(RoutePolicy(auth="session", paginated=True))
async def list_runners(
    request: Request, ctx: Session, session: SessionDep, page: Paging
) -> Page[api.RunnerOut]:
    """Runners by name, with their status judged from the last heartbeat (online after a
    beat in the last 45 s, offline after that, never_seen before the first register)."""
    return await api.list_runners(
        session, now=_clock(request).now(), cursor=page.cursor, limit=page.limit
    )


@router.post("/runners", status_code=201)
@route_policy(RoutePolicy(auth="session", idempotent=True, redact_on_replay=_TOKEN_ONCE))
async def create_runner(
    body: api.RunnerIn, request: Request, ctx: Session, session: SessionDep
) -> api.RunnerCreated:
    """A runner and its device token, shown once. 409 `runner_exists`."""
    return await api.create_runner(ctx, session, body, now=_clock(request).now())


@router.post("/runners/{id}/rotate-token")
@route_policy(RoutePolicy(auth="session", idempotent=True, redact_on_replay=_TOKEN_ONCE))
async def rotate_runner_token(
    id: UUID, request: Request, ctx: Session, session: SessionDep
) -> api.RunnerCreated:
    """A new device token, shown once; the old one stops and the runner's socket closes."""
    return await api.rotate_runner_token(ctx, session, id, now=_clock(request).now())


# --- Agent profiles ------------------------------------------------------------------------


@router.get("/agents/profiles")
@route_policy(RoutePolicy(auth="session", paginated=True))
async def list_profiles(
    ctx: Session, session: SessionDep, page: Paging, project_id: UUID | None = None
) -> Page[api.AgentProfileOut]:
    """Agent profiles by name, with their last health check; `project_id` narrows them to
    that project's agent (the project header, P1-06)."""
    return await api.list_profiles(
        session, cursor=page.cursor, limit=page.limit, project_id=project_id
    )


@router.post("/agents/profiles", status_code=201)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def register_profile(
    body: api.ProfileIn, request: Request, ctx: Session, session: SessionDep
) -> api.AgentProfileOut:
    """Register a Hermes profile. 422 `invalid_profile_name`, `invalid_profile`; 404 for
    an unknown runner or project; 409 `master_exists`, `project_agent_exists`,
    `profile_exists`."""
    return await api.register_profile(session, body, now=_clock(request).now())


@router.patch("/agents/profiles/{id}")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def update_profile(
    id: UUID, body: api.ProfilePatch, request: Request, ctx: Session, session: SessionDep
) -> api.AgentProfileOut:
    """Move a profile to another runner or endpoint, or pause it. 409 `stale_version`."""
    return await api.update_profile(session, id, body, now=_clock(request).now())


@router.get("/agents/profiles/{id}/tools")
@route_policy(RoutePolicy(auth="session"))
async def get_profile_tools(id: UUID, ctx: Session, session: SessionDep) -> api.ProfileToolsOut:
    """The profile's MCP servers from its last health check, read-only (FR-5.12): each
    matched against its project's allowlist, and its GitHub and Coolify tokens' reach."""
    return await api.profile_tools(session, id)


@router.post("/agents/profiles/{id}/health-check", status_code=202)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def check_profile_health(
    id: UUID, request: Request, ctx: Session, session: SessionDep
) -> api.HealthCheckAccepted:
    """Ask the profile's runner (or endpoint) for its health; the answer lands in the
    profile's `health` (reachable, authenticated, version)."""
    hub = request.app.state.runner_hub
    return await api.request_health_check(
        ctx, session, id, now=_clock(request).now(), client=hub.client()
    )


# --- Task packets (P2-02) ------------------------------------------------------------------


@router.get("/tasks/{task_id}/packet")
@route_policy(
    RoutePolicy(
        auth="session_or_key", scopes=frozenset({"tasks:read"}), project_param="lookup:tasks"
    )
)
async def get_task_packet(
    task_id: UUID,
    request: Request,
    session: SessionDep,
    query: Annotated[tools.TaskPacketQuery, Query()],
) -> TaskPacket:
    """The task's packet as a run would get it, with no token; the `get_task_packet`
    tool's twin. 404 for a task the caller cannot see."""
    raw = {**query.model_dump(), "task_id": task_id}
    found = await surface.rest_twin(request, session, tools.GET_TASK_PACKET, raw)
    assert isinstance(found, TaskPacket)  # noqa: S101  # the op's output model
    return found


# --- Runs (P2-04, FR-5.4, FR-5.5, FR-5.8) --------------------------------------------------

_READ_RUN = RoutePolicy(
    auth="session_or_key", scopes=frozenset({"tasks:read"}), project_param="lookup:runs"
)
_WRITE_RUN = RoutePolicy(
    auth="session_or_key",
    scopes=frozenset({"tasks:write"}),
    idempotent=True,
    project_param="lookup:runs",
)


@router.post("/tasks/{task_id}/run", status_code=202)
@route_policy(
    RoutePolicy(
        auth="session_or_key",
        scopes=frozenset({"tasks:write"}),
        idempotent=True,
        project_param="lookup:tasks",
    )
)
async def request_run(
    task_id: UUID, body: api.RunRequestIn, request: Request, session: SessionDep
) -> api.RunRequested:
    """Run the task's agent (the Run button): the run is queued and its `dispatch_run`
    starts as soon as the project has a free slot (two at a time per project). 409
    `run_already_active`, `status_not_runnable`, `no_ready_profile`; 422
    `label_not_runnable`."""
    run_id = await api.request_run(
        task_id,
        api.RunKind(body.kind),
        ctx=principal_of(request).workspace_context(),
        session=session,
        now=_clock(request).now(),
    )
    return api.RunRequested(run_id=run_id)


@router.get("/runs/{run_id}")
@route_policy(_READ_RUN)
async def get_run(run_id: UUID, session: SessionDep) -> api.RunOut:
    """The run: its status, stop reason and times (the run view's header)."""
    return await api.get_run(session, run_id)


@router.get("/runs/{run_id}/events")
@route_policy(_READ_RUN)
async def list_run_events(
    run_id: UUID,
    session: SessionDep,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=api.RUN_EVENTS_LIMIT_MAX)] = api.RUN_EVENTS_LIMIT_DEFAULT,
) -> api.RunEventsPage:
    """The run's log and events after `after_seq`, in the order they were stored (FR-5.5);
    the next page asks `after_seq=next_after_seq`."""
    return await api.run_events_page(session, run_id, after_seq=after_seq, limit=limit)


@router.post("/runs/{run_id}/cancel", status_code=202)
@route_policy(_WRITE_RUN)
async def cancel_run(run_id: UUID, request: Request, session: SessionDep) -> api.RunOut:
    """Stop (FR-5.5): a queued run ends at once; a running one is stopped by its workflow
    through the agent's adapter (the api never calls out). An ended run is left as it is."""
    return await api.cancel_run(
        principal_of(request).workspace_context(),
        run_id,
        now=_clock(request).now(),
        session=session,
    )


@router.post("/runs/{run_id}/result")
@route_policy(_WRITE_RUN)
async def post_result(
    run_id: UUID, body: tools.PostResultBody, request: Request, session: SessionDep
) -> api.ResultOut:
    """The run's result, posted with its task token; the `post_result` tool's twin. 403
    `run_mismatch` for another run's token; 409 `run_not_active` once the run ended. A
    second post answers the first result."""
    raw = {**body.model_dump(), "run_id": run_id}
    posted = await surface.rest_twin(request, session, tools.POST_RESULT, raw)
    assert isinstance(posted, api.ResultOut)  # noqa: S101  # the op's output model
    return posted


# --- Questions and approvals (P2-05, FR-5.6, FR-5.7) ----------------------------------------

_HUMAN_WAIT = RoutePolicy(
    auth="session_or_key",
    scopes=frozenset({"tasks:write"}),
    idempotent=False,
    not_idempotent_reason=(
        "idempotent inside invoke on the Idempotency-Key header, as on MCP; the long poll"
        " runs after that transaction commits, so the route holds no session"
    ),
    project_param="lookup:runs",
)


@router.post("/runs/{run_id}/questions")
@route_policy(_HUMAN_WAIT)
async def ask_human(run_id: UUID, body: tools.AskHumanBody, request: Request) -> api.HumanWaitOut:
    """Ask the human (the `ask_human` tool's twin): the task waits on the human, and the
    call waits up to the long poll for the answer, else answers `pending` with the
    question's id for the re-send. A key with no run answers `denied`."""
    raw = {**body.model_dump(), "run_id": run_id}
    asked = await surface.rest_twin_detached(request, tools.ASK_HUMAN, raw)
    assert isinstance(asked, api.HumanWaitOut)  # noqa: S101  # the op's output model
    return asked


@router.post("/runs/{run_id}/approvals")
@route_policy(_HUMAN_WAIT)
async def request_approval(
    run_id: UUID, body: tools.RequestApprovalBody, request: Request
) -> api.HumanWaitOut:
    """Ask before an action (the `request_approval` tool's twin): `approved`, `denied`, or
    `pending` with the approval's id once the long poll runs out. A key with no run
    answers `denied`."""
    raw = {**body.model_dump(), "run_id": run_id}
    asked = await surface.rest_twin_detached(request, tools.REQUEST_APPROVAL, raw)
    assert isinstance(asked, api.HumanWaitOut)  # noqa: S101  # the op's output model
    return asked
