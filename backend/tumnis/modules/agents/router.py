"""agents REST (P1-04, FR-5.1, FR-5.9): runners and agent profiles, session only (admin);
and the digests' twins (P2-03), for a session or a key with `tasks:read`.

`/v1/runners` and `/v1/agents/profiles` (not under one module prefix: the plan's paths).
Creating a runner and rotating its token answer the device token once; an idempotent
replay answers without it (`redact_on_replay`).
"""

from typing import Annotated, Final
from uuid import UUID

from fastapi import Depends, Query, Request
from pydantic import BaseModel, StringConstraints

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


# --- Ask the agent, Activity and the dashboard feed (P2-17, FR-2.5, FR-2.6, FR-1.5) ---------


@router.post("/projects/{project_id}/ask", status_code=201)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def ask(
    project_id: UUID, body: api.AskIn, request: Request, ctx: Session, session: SessionDep
) -> api.AskOut:
    """Ask the project's agent (the composer's Ask the agent toggle): the question becomes
    an AI task (source `ask`) and its run starts at once; the run's result is the answer.
    404 for an unknown project; 409 `no_ready_profile` (nothing is kept) when the project
    has no ready agent."""
    return await api.ask(
        session, ctx.actor, project_id, body.question, ctx=ctx, now=_clock(request).now()
    )


@router.get("/projects/{project_id}/activity")
@route_policy(RoutePolicy(auth="session", paginated=True))
async def list_activity(
    project_id: UUID, ctx: Session, session: SessionDep, page: Paging
) -> Page[api.ActivityItem]:
    """The project's Activity view (FR-2.6): its task runs, their results and its audit
    trail, newest first, a page at a time. 400 `invalid_cursor`; 404 for an unknown
    project."""
    return await api.activity(session, project_id, cursor=page.cursor, limit=page.limit)


@router.get("/agents/feed")
@route_policy(
    RoutePolicy(
        auth="session",
        unpaginated_reason="four groups of at most ten runs each, the dashboard's feed",
    )
)
async def get_agent_feed(ctx: Session, session: SessionDep) -> api.AgentFeedOut:
    """The dashboard's agent activity (FR-1.5): task runs running, waiting on the human,
    finished and failed, the newest ten of each."""
    return await api.agent_feed(session)


# --- The kill switch (P2-09, SAF-4) ------------------------------------------------------------


class ProjectPauseIn(BaseModel):
    reason: api.ReasonText


class ProjectResumeIn(BaseModel):
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None


@router.get("/agents/pause")
@route_policy(RoutePolicy(auth="session"))
async def get_pauses(ctx: Session, session: SessionDep) -> api.PausesOut:
    """What is paused now: the whole workspace and each paused project (the kill switch's
    state in the app)."""
    return await api.open_pauses(session)


@router.post("/agents/pause")
@route_policy(RoutePolicy(auth="session_or_key", scopes=frozenset({"delegate"}), idempotent=True))
async def pause_agents(
    body: tools.PauseBody, request: Request, session: SessionDep
) -> api.PauseOut:
    """The kill switch: pause every agent (`scope` workspace) or one project's, with a
    reason. Running runs are cancelled through their agent, queued runs held, new runs
    refused (409 `agents_paused`) until a person resumes in the app. The person's control
    in the app, and the `pause_agents` tool's twin for the master key (403 `master_only`
    for any other key)."""
    principal = principal_of(request)
    if principal.kind == "session":
        inp = api.PauseIn(scope=body.scope, project_id=body.project_id, reason=body.reason)
        return await api.pause(
            principal.workspace_context(), inp, now=_clock(request).now(), session=session
        )
    paused = await surface.rest_twin(request, session, tools.PAUSE_AGENTS, body.model_dump())
    assert isinstance(paused, api.PauseOut)  # noqa: S101  # the op's output model
    return paused


@router.post("/agents/resume")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def resume_agents(
    body: api.ResumeIn, request: Request, ctx: Session, session: SessionDep
) -> api.ResumeOut:
    """Let the agents run again (a person, in the app; no key or tool can): held runs
    start. Nothing paused: nothing changes."""
    return await api.resume(ctx, body, now=_clock(request).now(), session=session)


@router.post("/projects/{project_id}/pause")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def pause_project(
    project_id: UUID, body: ProjectPauseIn, request: Request, ctx: Session, session: SessionDep
) -> api.PauseOut:
    """Pause one project's agents, with a reason: its running runs are cancelled, its
    queued runs held and its new runs refused until it is resumed. 404 for an unknown
    project."""
    inp = api.PauseIn(scope="project", project_id=project_id, reason=body.reason)
    return await api.pause(ctx, inp, now=_clock(request).now(), session=session)


@router.post("/projects/{project_id}/resume")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def resume_project(
    project_id: UUID, body: ProjectResumeIn, request: Request, ctx: Session, session: SessionDep
) -> api.ResumeOut:
    """Let one project's agents run again: its held runs start, unless all agents are
    still paused."""
    inp = api.ResumeIn(scope="project", project_id=project_id, reason=body.reason)
    return await api.resume(ctx, inp, now=_clock(request).now(), session=session)


# --- Digests (P2-03): the get_project_digest and get_workspace_digest tools' twins ----------

DIGEST_READ = frozenset({"tasks:read"})
Since = Annotated[
    str | None,
    Query(max_length=512, description="The previous answer's `next_cursor`; acknowledges it"),
]
Limit = Annotated[int, Query(ge=1, le=1000)]


@router.get("/digests/project/{project_id}")
@route_policy(
    RoutePolicy(auth="session_or_key", scopes=DIGEST_READ, project_param="path:project_id")
)
async def get_project_digest(
    project_id: UUID,
    request: Request,
    session: SessionDep,
    *,
    since: Since = None,
    limit: Limit = tools.DEFAULT_LIMIT,
    schema_version: Annotated[int | None, Query()] = None,
) -> api.DigestOut:
    """What changed in the project since the caller's last acknowledged digest (the
    `get_project_digest` tool's twin); 400 `invalid_cursor` for a cursor issued to another
    caller or digest."""
    raw = {
        "project_id": project_id,
        "since": since,
        "limit": limit,
        "schema_version": schema_version,
    }
    found = await surface.rest_twin(request, session, tools.GET_PROJECT_DIGEST, raw)
    assert isinstance(found, api.DigestOut)  # noqa: S101  # the op's output model
    return found


@router.get("/digests/workspace")
@route_policy(RoutePolicy(auth="session_or_key", scopes=DIGEST_READ))
async def get_workspace_digest(
    request: Request,
    session: SessionDep,
    since: Since = None,
    limit: Limit = tools.DEFAULT_LIMIT,
    schema_version: Annotated[int | None, Query()] = None,
) -> api.DigestOut:
    """The workspace-wide signals since the caller's last acknowledged digest (the
    `get_workspace_digest` tool's twin)."""
    raw = {"since": since, "limit": limit, "schema_version": schema_version}
    found = await surface.rest_twin(request, session, tools.GET_WORKSPACE_DIGEST, raw)
    assert isinstance(found, api.DigestOut)  # noqa: S101  # the op's output model
    return found
