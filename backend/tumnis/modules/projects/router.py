"""projects FastAPI router under /v1/projects; thin calls into api.py (P0-17, FR-2.1).

Reads take a session or a key with `tasks:read`; a project-limited key sees only its
projects (the list filters to them, a detail of another project is 404, R-28). Writes are
session-only in phase 0 (plan default) and idempotent; versioned ones answer 409
`stale_version` with the current project.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request
from pydantic import BaseModel

from tumnis.core.clock import Clock
from tumnis.core.idempotency import SessionDep
from tumnis.core.pagination import Page, PageParams, page_params
from tumnis.core.principal import principal_of
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.versioning import Version
from tumnis.modules.projects import api

router = v1_router("projects", prefixed=True, tags=["projects"])

LIST = RoutePolicy(auth="session_or_key", scopes=frozenset({"tasks:read"}), paginated=True)
READ_ONE = RoutePolicy(
    auth="session_or_key", scopes=frozenset({"tasks:read"}), project_param="path:project_id"
)
WRITE = RoutePolicy(auth="session", idempotent=True)


class VersionIn(BaseModel):
    version: Version


class ReorderIn(BaseModel):
    after_id: UUID | None = None
    before_id: UUID | None = None
    version: Version


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


@router.get("")
@route_policy(LIST)
async def list_projects(
    request: Request,
    session: SessionDep,
    page: Annotated[PageParams, Depends(page_params)],
    include_archived: Annotated[bool, Query()] = False,
) -> Page[api.ProjectOut]:
    """Projects in board order; archived ones with `include_archived=true`."""
    return await api.list_projects(
        session,
        include_archived=include_archived,
        cursor=page.cursor,
        limit=page.limit,
        project_ids=principal_of(request).project_ids,
        now=_clock(request).now(),
    )


@router.post("", status_code=201)
@route_policy(WRITE)
async def create_project(
    body: api.ProjectCreateIn, request: Request, session: SessionDep
) -> api.ProjectOut:
    return await api.create_project(
        session, principal_of(request).actor, body, now=_clock(request).now()
    )


@router.get("/{project_id}")
@route_policy(READ_ONE)
async def get_project(project_id: UUID, request: Request, session: SessionDep) -> api.ProjectOut:
    return await api.get_project(session, project_id, now=_clock(request).now())


@router.patch("/{project_id}")
@route_policy(WRITE)
async def update_project(
    project_id: UUID, body: api.ProjectPatch, request: Request, session: SessionDep
) -> api.ProjectOut:
    return await api.update_project(
        session,
        principal_of(request).actor,
        project_id,
        body,
        body.version,
        now=_clock(request).now(),
    )


@router.post("/{project_id}/archive")
@route_policy(WRITE)
async def archive_project(
    project_id: UUID, body: VersionIn, request: Request, session: SessionDep
) -> api.ProjectOut:
    return await api.archive_project(
        session, principal_of(request).actor, project_id, body.version, now=_clock(request).now()
    )


@router.post("/{project_id}/unarchive")
@route_policy(WRITE)
async def unarchive_project(
    project_id: UUID, body: VersionIn, request: Request, session: SessionDep
) -> api.ProjectOut:
    return await api.unarchive_project(
        session, principal_of(request).actor, project_id, body.version, now=_clock(request).now()
    )


@router.post("/{project_id}/reorder")
@route_policy(WRITE)
async def reorder_project(
    project_id: UUID, body: ReorderIn, request: Request, session: SessionDep
) -> api.ProjectOut:
    """Moves the project between two neighbours (either may be null: an open end)."""
    return await api.reorder_project(
        session,
        principal_of(request).actor,
        project_id,
        after_id=body.after_id,
        before_id=body.before_id,
        version=body.version,
        now=_clock(request).now(),
    )
