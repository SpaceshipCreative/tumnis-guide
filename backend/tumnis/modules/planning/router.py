"""planning FastAPI routers; thin calls into api.py (P1-10).

- `router` under /v1/plan: `GET /{day}/calendar`, the day's working window, events from
  every account and free blocks (the dashboard's calendar strip).
- `settings_router` under /v1/settings: `GET/PUT /working-hours`, Monday to Friday start
  and end (Settings > Working hours). `create_app` mounts it before the generic
  `/v1/settings/{section}` route.

All session-only.
"""

from datetime import date
from typing import Annotated

from fastapi import Depends

from tumnis.core.audit_router import require_session
from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.planning import api

router = v1_router("planning", prefix="/plan", tags=["planning"])
settings_router = v1_router("planning", prefix="/settings", tags=["settings"])

Session = Annotated[WorkspaceContext, Depends(require_session)]


@router.get("/{day}/calendar")
@route_policy(RoutePolicy(auth="session"))
async def get_day_calendar(day: date, ctx: Session) -> api.DayCalendarOut:
    return await api.day_calendar(ctx, day)


@settings_router.get("/working-hours")
@route_policy(RoutePolicy(auth="session"))
async def get_working_hours(ctx: Session) -> api.WorkingHoursOut:
    return await api.get_working_hours(ctx)


@settings_router.put("/working-hours")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def put_working_hours(
    body: api.WorkingHoursIn, ctx: Session, session: SessionDep
) -> api.WorkingHoursOut:
    del session  # the request's transaction; the implementation writes in it
    return await api.put_working_hours(ctx, body)
