"""auth FastAPI routers; thin calls into api.py.

The workspace settings resource (R-14): GET and PUT /v1/settings/workspace (`auth="session"`,
PUT idempotent). Until P0-10's v1_router and route_policy and P0-13's sessions land, the
routes sit on a plain router under /v1 and take the signed-in context from the same session
seam as the audit routes (tumnis.core.audit_router.require_session: 401 until P0-13).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from tumnis.core.audit_router import require_session
from tumnis.core.errors import ProblemError
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.versioning import StaleVersion
from tumnis.modules.auth import api

settings_router = APIRouter(prefix="/v1/settings", tags=["settings"])

Session = Annotated[WorkspaceContext, Depends(require_session)]


@settings_router.get("/workspace")
async def get_workspace_settings(ctx: Session) -> api.WorkspaceSettingsOut:
    return await api.get_workspace_settings(ctx)


@settings_router.put("/workspace")
async def put_workspace_settings(
    body: api.WorkspaceSettingsIn, request: Request, ctx: Session
) -> api.WorkspaceSettingsOut:
    try:
        return await api.put_workspace_settings(ctx, body, now=request.app.state.clock.now())
    except StaleVersion as stale:
        detail = "The settings changed since you read them"
        raise ProblemError(409, "stale_version", detail, current=dict(stale.current)) from None
    except api.WorkspaceSettingsInvalid as invalid:
        raise ProblemError(422, invalid.code, str(invalid)) from None
