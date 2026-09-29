"""auth FastAPI routers; thin calls into api.py.

The workspace settings resource (R-14): GET and PUT /settings/workspace, mounted under /v1
by P0-10's v1_router with `route_policy(auth="session", idempotent=True)` once that lands;
until P0-13's session dependency exists, a request without a workspace context is 401.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from tumnis.core import tenancy
from tumnis.core.errors import ProblemError
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.versioning import StaleVersion
from tumnis.modules.auth import api

settings_router = APIRouter(prefix="/settings", tags=["settings"])


def workspace_context() -> WorkspaceContext:
    """The caller's workspace (P0-13 replaces this with the session dependency)."""
    ctx = tenancy.current()
    if ctx is None:
        raise ProblemError(401, "unauthenticated", "Sign in first")
    return ctx


Context = Annotated[WorkspaceContext, Depends(workspace_context)]


@settings_router.get("/workspace")
async def get_workspace_settings(ctx: Context) -> api.WorkspaceSettingsOut:
    return await api.get_workspace_settings(ctx)


@settings_router.put("/workspace")
async def put_workspace_settings(
    body: api.WorkspaceSettingsIn, request: Request, ctx: Context
) -> api.WorkspaceSettingsOut:
    try:
        return await api.put_workspace_settings(ctx, body, now=request.app.state.clock.now())
    except StaleVersion as stale:
        raise ProblemError(
            409,
            "stale_version",
            "The settings changed since you read them",
            current=dict(stale.current),
        ) from None
    except api.WorkspaceSettingsInvalid as invalid:
        raise ProblemError(422, invalid.code, str(invalid)) from None
