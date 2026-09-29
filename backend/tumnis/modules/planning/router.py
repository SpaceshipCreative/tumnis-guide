"""planning FastAPI routers; thin calls into api.py (P1-10).

- `router` under /v1/plan: `GET /{day}/calendar`, the day's working window in the
  workspace timezone, the events from every account and the free blocks between them (the
  dashboard's calendar strip).
- `settings_router` under /v1/settings: `GET/PUT /working-hours`, the start and end per
  weekday (Settings > Working hours); PUT saves the days it names at the week's `version`
  (stale: 409 `stale_version` with `current`; an end before the start or a repeated
  weekday: 422 `validation_error`). `create_app` mounts it before the generic
  `/v1/settings/{section}` route.

All session-only; the PUT is idempotent and runs in the request's transaction.
"""

from datetime import date
from typing import Annotated

from fastapi import Depends, Request

from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.errors import ProblemError
from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.planning import api

router = v1_router("planning", prefix="/plan", tags=["planning"])
settings_router = v1_router("planning", prefix="/settings", tags=["settings"])

Session = Annotated[WorkspaceContext, Depends(require_session)]


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


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
    body: api.WorkingHoursIn, request: Request, ctx: Session, session: SessionDep
) -> api.WorkingHoursOut:
    try:
        return await api.put_working_hours(ctx, body, now=_clock(request).now(), session=session)
    except api.WorkingHoursInvalid as invalid:
        raise ProblemError(422, invalid.code, str(invalid)) from None
