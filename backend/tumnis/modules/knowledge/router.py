"""knowledge FastAPI router under /v1/knowledge; thin calls into api.py.

Storage locations (P1-14, FR-15.7), all `auth="session"` (the Settings screen):
`GET /locations` (every location; a workspace has a handful, so a bare list),
`POST /locations` (201), `POST /locations/{storage_location_id}/test` (test the
connection now; drains queued writes when healthy), `POST
/locations/{storage_location_id}/default` (versioned),
and `PUT /projects/{project_id}/folder` (move an empty project folder to another
location). Each handler finds its row (404) before any rule about the body. Storage calls
take the deployment's SSRF policy from the settings.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel

from tumnis.core.audit_router import require_session
from tumnis.core.idempotency import SessionDep
from tumnis.core.net import NetPolicy
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.versioning import Version
from tumnis.modules.knowledge import api

router = v1_router("knowledge", prefixed=True, tags=["knowledge"])

Session = Annotated[WorkspaceContext, Depends(require_session)]


class DefaultIn(BaseModel):
    version: Version


class FolderIn(BaseModel):
    location_id: UUID


def _net(request: Request) -> NetPolicy:
    policy: NetPolicy = request.app.state.settings.net_policy()
    return policy


@router.get("/locations")
@route_policy(
    RoutePolicy(
        auth="session",
        unpaginated_reason="a workspace's storage locations: a handful, set up by hand",
    )
)
async def list_locations(ctx: Session) -> list[api.LocationOut]:
    async with tenant_session(ctx) as s:
        return await api.list_locations(s)


@router.post("/locations", status_code=201)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def create_location(
    body: api.LocationIn, request: Request, _ctx: Session, session: SessionDep
) -> api.LocationOut:
    return await api.create_location(session, body, net=_net(request))


@router.post("/locations/{storage_location_id}/test")
@route_policy(
    RoutePolicy(
        auth="session",
        idempotent=False,
        not_idempotent_reason="a connection test must reach the location again, never replay",
    )
)
async def test_location(
    storage_location_id: UUID, request: Request, ctx: Session
) -> api.LocationOut:
    async with tenant_session(ctx) as s:
        return await api.check_location(s, storage_location_id, net=_net(request))


@router.post("/locations/{storage_location_id}/default")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def set_default_location(
    storage_location_id: UUID, body: DefaultIn, _ctx: Session, session: SessionDep
) -> api.LocationOut:
    return await api.set_default_location(session, storage_location_id, body.version)


@router.put("/projects/{project_id}/folder")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def set_project_folder(
    project_id: UUID, body: FolderIn, request: Request, _ctx: Session, session: SessionDep
) -> api.ProjectFolderOut:
    return await api.set_project_location(session, project_id, body.location_id, net=_net(request))
