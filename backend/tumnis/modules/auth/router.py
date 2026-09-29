"""auth FastAPI routers; thin calls into api.py.

The workspace settings resource (R-14): GET and PUT /v1/settings/workspace (`auth="session"`,
PUT idempotent), declared on `v1_router` (P0-10). The signed-in context comes from the shared
session seam (tumnis.core.audit_router.require_session). A stale version raises
`StaleVersion`, which the problem handlers answer as 409 `stale_version` with `current`.
"""

from typing import Annotated

from fastapi import Depends, Request

from tumnis.core.audit_router import require_session
from tumnis.core.errors import ProblemError
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.auth import api

settings_router = v1_router("auth", prefix="/settings", tags=["settings"])

Session = Annotated[WorkspaceContext, Depends(require_session)]


@settings_router.get("/workspace")
@route_policy(RoutePolicy(auth="session"))
async def get_workspace_settings(ctx: Session) -> api.WorkspaceSettingsOut:
    return await api.get_workspace_settings(ctx)


@settings_router.put("/workspace")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def put_workspace_settings(
    body: api.WorkspaceSettingsIn, request: Request, ctx: Session
) -> api.WorkspaceSettingsOut:
    try:
        return await api.put_workspace_settings(ctx, body, now=request.app.state.clock.now())
    except api.WorkspaceSettingsInvalid as invalid:
        raise ProblemError(422, invalid.code, str(invalid)) from None
