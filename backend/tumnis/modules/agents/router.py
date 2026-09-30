"""agents REST (P1-04, FR-5.1, FR-5.9): runners and agent profiles, session only (admin);
and the digests' twins (P2-03), for a session or a key with `tasks:read`.

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
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.agents import api
from tumnis.modules.agents import mcp as tools

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
