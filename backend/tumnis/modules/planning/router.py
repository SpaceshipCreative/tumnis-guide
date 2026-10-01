"""planning FastAPI routers; thin calls into api.py (P1-10).

- `router` under /v1/plan: `GET /{day}/calendar`, the day's working window in the
  workspace timezone, the events from every account and the free blocks between them (the
  dashboard's calendar strip).
- `router`, P1-12: `GET /week/{monday}?project_id=`, one project's week (Monday to Sunday
  in the workspace timezone: windows, free blocks, the project's meetings and other busy
  time, due tasks, planned blocks, and the tasks left to schedule); `PATCH
  /{day}/items/{task_id}` `{block_start, block_end, version?}`, the view's only write: it
  upserts the task's block in the day's published plan (a `manual` plan when there is
  none); a block that is not free answers 409 `block_not_free` (or the violation's own
  code) with the current free blocks in `current`.
- `settings_router` under /v1/settings: `GET/PUT /working-hours`, the start and end per
  weekday (Settings > Working hours); PUT saves the days it names at the week's `version`
  (stale: 409 `stale_version` with `current`; an end before the start or a repeated
  weekday: 422 `validation_error`). `create_app` mounts it before the generic
  `/v1/settings/{section}` route.

P1-11, the daily plan: `GET /{day}` (the published plan with each item's live task
status and `blocked` flag, its issues and notice; 404 when the day has none), `POST
/replan` (202; enqueues `build_plan` with trigger `replan`), `POST
/{day}/items/{task_id}/accept|remove|swap` (`{with_task_id}`), `POST /{day}/accept-all`,
`GET /{day}/alternates` and `POST /{day}/issues/{plan_issue_id}/split|move`. Each write answers the
plan as it now is.

P1-18, close the day and local metrics (`root_router`, under /v1 itself): `GET
/day/{day}/summary` (what shipped, what agents finished, what is queued overnight and what
rolls over, for the workspace's local day), `POST /metrics/open` (204; the PWA's app-start
ping raises the `app_open` counter on the local day) and `GET /metrics/summary?from=&to=`
(the PRD's success metrics, each with its target, null where there is no data yet).

All session-only; every write is idempotent and runs in the request's transaction.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request, Response

from tumnis.core import modules
from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.errors import ProblemError
from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.planning import api
from tumnis.modules.planning import testing as _testing  # noqa: F401  # registers `planner-tick`

router = v1_router("planning", prefix="/plan", tags=["planning"])
settings_router = v1_router("planning", prefix="/settings", tags=["settings"])
# P1-18: routes outside /plan (`/day/{day}/summary`, `/metrics/open`, `/metrics/summary`).
# The metrics live here, not in usage: usage only subscribes and calls no other module
# (the search-usage-subscribe-only contract), and the summary reads tasks, plans and runs.
root_router = v1_router("planning", tags=["planning"])
_USAGE_ON = Depends(modules.require_module("usage"))  # the metrics follow usage's switch too

Session = Annotated[WorkspaceContext, Depends(require_session)]


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


@router.get("/{day}/calendar")
@route_policy(RoutePolicy(auth="session"))
async def get_day_calendar(day: date, ctx: Session) -> api.DayCalendarOut:
    return await api.day_calendar(ctx, day)


@router.get("/week/{monday}")
@route_policy(RoutePolicy(auth="session"))
async def get_project_week(monday: date, project_id: UUID, ctx: Session) -> api.WeekOut:
    try:
        return await api.project_week(ctx, monday, project_id)
    except api.NotAMonday as invalid:
        raise ProblemError(422, invalid.code, str(invalid)) from None


@router.patch("/{day}/items/{task_id}")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def schedule_plan_item(  # noqa: PLR0917  # path, body and the injected request
    day: date,
    task_id: UUID,
    body: api.ManualBlockIn,
    request: Request,
    ctx: Session,
    session: SessionDep,
) -> api.PlanItemOut:
    return await api.schedule_block(
        ctx, day, task_id, body, now=_clock(request).now(), session=session
    )


# --- The daily plan (P1-11) ------------------------------------------------------------------


@router.post("/replan", status_code=202)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def replan(body: api.ReplanIn, request: Request, ctx: Session) -> api.ReplanAccepted:
    return await api.request_replan(ctx, body, now=_clock(request).now())


@router.get("/{day}")
@route_policy(RoutePolicy(auth="session"))
async def get_plan(day: date, ctx: Session) -> api.PlanOut:
    return await api.get_plan(ctx, day)


@router.get("/{day}/alternates")
@route_policy(
    RoutePolicy(
        auth="session",
        unpaginated_reason="at most 20 tasks a swap can bring in (api.MAX_ALTERNATES)",
    )
)
async def get_alternates(day: date, ctx: Session) -> list[api.AlternateOut]:
    return await api.alternates(ctx, day)


@router.post("/{day}/accept-all")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def accept_all(day: date, request: Request, ctx: Session, session: SessionDep) -> api.PlanOut:
    return await api.accept_all(ctx, day, now=_clock(request).now(), session=session)


@router.post("/{day}/items/{task_id}/accept")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def accept_item(
    day: date, task_id: UUID, request: Request, ctx: Session, session: SessionDep
) -> api.PlanOut:
    return await api.accept_item(ctx, day, task_id, now=_clock(request).now(), session=session)


@router.post("/{day}/items/{task_id}/remove")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def remove_item(
    day: date, task_id: UUID, request: Request, ctx: Session, session: SessionDep
) -> api.PlanOut:
    return await api.remove_item(ctx, day, task_id, now=_clock(request).now(), session=session)


@router.post("/{day}/items/{task_id}/swap")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def swap_item(  # noqa: PLR0917  # path, body and the injected request
    day: date,
    task_id: UUID,
    body: api.SwapIn,
    request: Request,
    ctx: Session,
    session: SessionDep,
) -> api.PlanOut:
    return await api.swap_item(ctx, day, task_id, body, now=_clock(request).now(), session=session)


@router.post("/{day}/issues/{plan_issue_id}/split")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def split_issue(
    day: date, plan_issue_id: UUID, request: Request, ctx: Session, session: SessionDep
) -> api.PlanOut:
    return await api.resolve_issue(
        ctx, day, plan_issue_id, "split", now=_clock(request).now(), session=session
    )


@router.post("/{day}/issues/{plan_issue_id}/move")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def move_issue(
    day: date, plan_issue_id: UUID, request: Request, ctx: Session, session: SessionDep
) -> api.PlanOut:
    return await api.resolve_issue(
        ctx, day, plan_issue_id, "move", now=_clock(request).now(), session=session
    )


# --- Close the day and local metrics (P1-18) -------------------------------------------------


@root_router.get("/day/{day}/summary")
@route_policy(RoutePolicy(auth="session"))
async def get_day_summary(day: date, ctx: Session) -> api.DaySummaryOut:
    return await api.day_summary(ctx, day)


@root_router.post("/metrics/open", status_code=204, dependencies=[_USAGE_ON])
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def record_app_open(request: Request, ctx: Session, session: SessionDep) -> Response:
    await api.record_app_open(ctx, now=_clock(request).now(), session=session)
    return Response(status_code=204)


@root_router.get("/metrics/summary", dependencies=[_USAGE_ON])
@route_policy(RoutePolicy(auth="session"))
async def get_metrics_summary(
    request: Request,
    ctx: Session,
    day_from: Annotated[date, Query(alias="from")],
    day_to: Annotated[date, Query(alias="to")],
) -> api.MetricsSummaryOut:
    if day_to < day_from:
        raise ProblemError(422, "invalid_range", "`to` is before `from`")
    return await api.metrics_summary(ctx, day_from, day_to, now=_clock(request).now())


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
