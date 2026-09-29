"""coolify FastAPI router under /v1/coolify; thin calls into api.py (P2-14, FR-12.2).

`GET /v1/coolify/status`: each project that links Coolify applications with each
application's last polled deployment and open-PR previews, for the project cards and the
Connections rail; `project_id` narrows it to one project. A session or a key with
`tasks:read` (project reads, A10); a project-limited key sees only its projects (R-28).
Nothing here calls Coolify: the worker polls, this reads the stored status.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Query, Request

from tumnis.core.idempotency import SessionDep
from tumnis.core.principal import principal_of
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.modules.coolify import api

router = v1_router("coolify", prefixed=True, tags=["coolify"])

STATUS = RoutePolicy(
    auth="session_or_key",
    scopes=frozenset({"tasks:read"}),
    project_param="query:project_id",
    unpaginated_reason="one entry per project that links a Coolify app (3 to 10 projects)",
)


@router.get("/status")
@route_policy(STATUS)
async def list_deploy_status(
    request: Request,
    session: SessionDep,
    project_id: Annotated[UUID | None, Query()] = None,
) -> list[api.ProjectDeployStatus]:
    """Deploy status per project in board order; projects without apps are left out."""
    allowed = principal_of(request).project_ids
    if project_id is not None:
        allowed = frozenset({project_id}) if allowed is None else allowed & {project_id}
    return await api.deploy_status(session, project_ids=allowed)
