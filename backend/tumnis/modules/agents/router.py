"""agents REST (P1-04, FR-5.1, FR-5.9): runners and agent profiles, session only (admin).

`/v1/runners` and `/v1/agents/profiles` (not under one module prefix: the plan's paths).
Creating a runner and rotating its token answer the device token once; an idempotent
replay answers without it (`redact_on_replay`).
"""

from typing import Annotated, Final
from uuid import UUID

from fastapi import Depends, Request

from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.idempotency import SessionDep
from tumnis.core.pagination import Page, PageParams, page_params
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.agents import api

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
    ctx: Session, session: SessionDep, page: Paging
) -> Page[api.AgentProfileOut]:
    """Agent profiles by name, with their last health check."""
    return await api.list_profiles(session, cursor=page.cursor, limit=page.limit)


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


@router.get("/agents/profiles/{profile_id}/tools")
@route_policy(RoutePolicy(auth="session"))
async def get_profile_tools(
    profile_id: UUID, ctx: Session, session: SessionDep
) -> api.ProfileToolsOut:
    """The profile's MCP servers from its last health check, read-only (FR-5.12): each
    matched against its project's allowlist, and its GitHub and Coolify tokens' reach."""
    return await api.profile_tools(session, profile_id)


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
