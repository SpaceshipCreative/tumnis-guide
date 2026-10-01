"""planning public functions and DTOs; the only file other modules may import.

Working hours and the day calendar (P1-10, FR-4.7, FR-1.3, REL-6):

- Working hours are one `working_hours` row per weekday (local start and end). A weekday
  without a row works the default 09:00 to 18:00, so Monday to Friday are always present
  in `get_working_hours` whether or not a row was ever written. The week is one resource
  with one version: the sum of its rows' versions (each write bumps a row, so the sum only
  grows), checked under a per-workspace lock.
- `day_calendar(ctx, day)`: the working window in the workspace timezone, the day's events
  from every connection (`calendar.api.events_between`) with the account each came from,
  and the free blocks between the busy ones. Computed on read and cached (`free_blocks`
  namespace, the architecture's name), not stored: a stored copy would need its own
  invalidation on three inputs. Every entry carries two tags, dropped on commit by the
  writers of those inputs: `free_blocks_tag` (working hours saved here, `calendar.synced`
  in events.py) and `auth.workspace_settings_tag` (a timezone change).
"""

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import Table, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import audit, live
from tumnis.core.cache import CacheKey, CacheSpec, invalidate_on_commit, register_cache
from tumnis.core.clock import local_to_utc
from tumnis.core.errors import ProblemError
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import Interval
from tumnis.core.versioning import NotFound, StaleVersion, Version
from tumnis.modules.auth import api as auth
from tumnis.modules.calendar import api as calendar
from tumnis.modules.integrations import api as integrations
from tumnis.modules.planning.models import DailyPlan, PlanItem, WorkingHours
from tumnis.modules.planning.rules import (
    DEFAULT_HOURS,
    EventDTO,
    PlanTask,
    ProjectLink,
    Violation,
    event_matches_project,
    validate_manual_block,
    working_window,
)
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks

FREE_BLOCKS_CACHE: Final = "free_blocks"  # the day calendar's cache namespace
DAY_CALENDAR_TTL_S: Final = 300.0  # bounds what no invalidation reaches (event writes
# outside a sync, such as a deselected calendar, emit no calendar.synced)
_CACHE = register_cache(
    CacheSpec(
        FREE_BLOCKS_CACHE,
        scope="workspace",
        ttl_s=DAY_CALENDAR_TTL_S,
        invalidated_by=(
            "calendar.synced",
            "put_working_hours",
            "put_workspace_settings (auth.workspace_settings_tag)",
        ),
    )
)
_HOURS: Table = WorkingHours.__table__  # type: ignore[assignment]
_PLANS: Table = DailyPlan.__table__  # type: ignore[assignment]
_ITEMS: Table = PlanItem.__table__  # type: ignore[assignment]
MANUAL_REASON: Final = "Scheduled from the calendar"
# Open statuses a task can be scheduled from in the Calendar view.
SCHEDULABLE_STATUSES: Final = frozenset({"backlog", "today", "in_progress"})
# Violations that mean "that time is not free" (the view's one refusal message).
NOT_FREE: Final = frozenset({"block_outside_free_time", "blocks_overlap"})
_WEEK_LOCK: Final = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
SECTION: Final = "working-hours"


def day_calendar_key(workspace_id: UUID, day: date) -> CacheKey:
    """The cache entry of one day's calendar."""
    return CacheKey.for_workspace(workspace_id, FREE_BLOCKS_CACHE, day.isoformat())


def free_blocks_tag(workspace_id: UUID) -> str:
    """Tags every day calendar entry of the workspace; invalidating it drops them all."""
    return f"ws:{workspace_id}:{FREE_BLOCKS_CACHE}"


# --- Working hours ---------------------------------------------------------------------------

# A local wall time, "HH:MM" (24-hour).
LocalTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
Weekday = Annotated[int, Field(ge=0, le=6)]  # 0 = Monday


class WorkingDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weekday: Weekday
    start: LocalTime
    end: LocalTime


class WorkingHoursOut(BaseModel):
    days: list[WorkingDay]  # by weekday; Monday to Friday always present
    version: int  # the week's version: one optimistic lock for the resource


class WorkingHoursIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: Annotated[list[WorkingDay], Field(min_length=1, max_length=7)]
    version: Version


class WorkingHoursInvalid(ValueError):  # noqa: N818  # carries the problem code
    """Hours the resource refuses (422 `validation_error`)."""

    code = "validation_error"


def _hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def _time(value: str) -> time:
    return time.fromisoformat(value)


def _week(rows: Sequence[Any]) -> WorkingHoursOut:
    stored = {row.weekday: row for row in rows}
    weekdays = sorted({*range(5), *stored})
    days = [
        WorkingDay(
            weekday=weekday,
            start=_hhmm(stored[weekday].start_local if weekday in stored else DEFAULT_HOURS[0]),
            end=_hhmm(stored[weekday].end_local if weekday in stored else DEFAULT_HOURS[1]),
        )
        for weekday in weekdays
    ]
    return WorkingHoursOut(days=days, version=sum(row.version for row in rows))


async def _rows(s: AsyncSession, *, lock: bool = False) -> list[Any]:
    stmt = select(_HOURS).where(_HOURS.c.deleted_at.is_(None)).order_by(_HOURS.c.weekday)
    return list((await s.execute(stmt.with_for_update() if lock else stmt)).all())


def hours_by_weekday(rows: Sequence[Any]) -> dict[int, tuple[time, time]]:
    return {row.weekday: (row.start_local, row.end_local) for row in rows}


async def get_working_hours(
    ctx: WorkspaceContext, *, session: AsyncSession | None = None
) -> WorkingHoursOut:
    """Monday to Friday (the default where no row was saved) and any weekend day with its
    own hours, with the week's version."""
    async with session_for(ctx, session) as s:
        return _week(await _rows(s))


def _validate(body: WorkingHoursIn) -> None:
    weekdays = [day.weekday for day in body.days]
    if len(set(weekdays)) != len(weekdays):
        raise WorkingHoursInvalid("each weekday appears once")
    for day in body.days:
        if _time(day.end) <= _time(day.start):
            raise WorkingHoursInvalid(f"weekday {day.weekday}: the end comes after the start")


async def put_working_hours(
    ctx: WorkspaceContext,
    body: WorkingHoursIn,
    *,
    now: datetime,
    session: AsyncSession | None = None,
) -> WorkingHoursOut:
    """Saves the weekdays `body` names at the week's `version` (stale: StaleVersion with the
    current week, 409); days it leaves out keep their hours. Refuses a repeated weekday or
    an end at or before the start (WorkingHoursInvalid, 422). Audited `settings.changed`
    and every cached day calendar of the workspace dropped, both on commit."""
    _validate(body)
    async with session_for(ctx, session) as s:
        await s.execute(_WEEK_LOCK, {"key": f"working_hours:{ctx.workspace_id}"})
        rows = await _rows(s, lock=True)
        current = _week(rows)
        if current.version != body.version:
            raise StaleVersion(current=current.model_dump(mode="json"))
        stored = {row.weekday: row for row in rows}
        changed: list[int] = []
        for day in body.days:
            start, end = _time(day.start), _time(day.end)
            row = stored.get(day.weekday)
            if row is None:
                await s.execute(
                    _HOURS.insert().values(weekday=day.weekday, start_local=start, end_local=end)
                )
            elif (row.start_local, row.end_local) != (start, end):
                await s.execute(
                    update(_HOURS)
                    .where(_HOURS.c.id == row.id)
                    .values(start_local=start, end_local=end)
                )
            else:
                continue
            changed.append(day.weekday)
        if changed:
            await audit.record(
                s,
                "settings.changed",
                target=("workspace", ctx.workspace_id),
                details={"section": SECTION, "fields": [f"weekday_{d}" for d in sorted(changed)]},
                occurred_at=now,
            )
            await invalidate_on_commit(s, tag=free_blocks_tag(ctx.workspace_id))
            live.mark_changed(s, "settings", ctx.workspace_id)
        return _week(await _rows(s))


# --- The day calendar ------------------------------------------------------------------------


class WindowOut(BaseModel):
    start: datetime
    end: datetime


class DayEventOut(BaseModel):
    title: str | None
    start: datetime
    end: datetime
    busy: bool
    account: str  # the calendar account the event came from


class FreeBlockOut(BaseModel):
    start: datetime
    end: datetime
    minutes: int


class DayCalendarOut(BaseModel):
    timezone: str  # the workspace's IANA zone the window was computed in
    window: WindowOut | None  # None: no working hours that day
    events: list[DayEventOut]
    free_blocks: list[FreeBlockOut]


async def day_calendar(ctx: WorkspaceContext, day: date) -> DayCalendarOut:
    """The day's working window, events and free blocks in the workspace timezone (a
    weekend day has no window; Re-plan is P1-11's). From the cache when it holds the day;
    otherwise computed in one transaction and cached."""
    key = day_calendar_key(ctx.workspace_id, day)
    cached = await _CACHE.get(key)
    if cached is not None:
        return DayCalendarOut.model_validate_json(cached)
    token = _CACHE.token()
    zone = (await auth.get_workspace_settings(ctx)).timezone
    tz = ZoneInfo(zone)
    day_start = local_to_utc(day, time(0), tz)
    day_end = local_to_utc(day + timedelta(days=1), time(0), tz)
    async with tenant_session(ctx) as s:
        hours = hours_by_weekday(await _rows(s))
        events = await calendar.events_between(ctx, day_start, day_end, session=s)
        accounts = await integrations.connection_accounts(s, {e.connection_id for e in events})
    window = working_window(day, tz, hours, replan=False)
    busy = [Interval(e.start_at, e.end_at) for e in events if e.busy and e.end_at > e.start_at]
    out = DayCalendarOut(
        timezone=zone,
        window=None if window is None else WindowOut(start=window.start, end=window.end),
        events=[
            DayEventOut(
                title=e.title,
                start=e.start_at,
                end=e.end_at,
                busy=e.busy,
                account=accounts.get(e.connection_id, ""),
            )
            for e in events
        ],
        free_blocks=[
            FreeBlockOut(start=b.start, end=b.end, minutes=b.minutes)
            for b in calendar.free_blocks(window, busy)
        ],
    )
    tags = (free_blocks_tag(ctx.workspace_id), auth.workspace_settings_tag(ctx.workspace_id))
    await _CACHE.fill(key, out.model_dump_json().encode(), since=token, tags=tags)
    return out


# --- The project week view and manual blocks (P1-12) -----------------------------------------


class WeekEventOut(BaseModel):
    title: str | None  # None: busy time that is not the project's (shown as `Busy`)
    start: datetime
    end: datetime
    busy: bool
    matched: bool  # an attendee is one of the project's person or domain links


class TaskRefOut(BaseModel):
    id: UUID
    title: str
    label: str | None
    status: str
    estimate_minutes: int | None
    due_on: date | None


class PlannedBlockOut(BaseModel):
    task_id: UUID | None  # None: a block planned for another project's task
    title: str | None
    start: datetime
    end: datetime


class WeekDayOut(BaseModel):
    day: date
    window: WindowOut | None
    free_blocks: list[FreeBlockOut]
    events: list[WeekEventOut]
    due: list[TaskRefOut]
    planned: list[PlannedBlockOut]


class WeekOut(BaseModel):
    monday: date
    timezone: str  # the workspace's IANA zone the days were computed in
    days: list[WeekDayOut]  # Monday to Sunday
    unscheduled: list[TaskRefOut]  # the project's open Human and Hybrid tasks with an
    # estimate and no block this week: what the view offers to schedule


class ManualBlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_start: AwareDatetime
    block_end: AwareDatetime
    version: Version | None = None  # the plan item's version when moving a scheduled block


class PlanItemOut(BaseModel):
    plan_id: UUID
    task_id: UUID
    day: date
    position: int
    reason: str
    block_start: datetime | None
    block_end: datetime | None
    version: int


class NotAMonday(ValueError):  # noqa: N818  # carries the problem code
    code = "validation_error"


def _day_bounds(day: date, tz: ZoneInfo) -> Interval:
    return Interval(
        local_to_utc(day, time(0), tz), local_to_utc(day + timedelta(days=1), time(0), tz)
    )


def _task_ref(task: tasks.TaskOut) -> TaskRefOut:
    return TaskRefOut(
        id=task.id,
        title=task.title,
        label=task.label,
        status=task.status,
        estimate_minutes=task.estimate_minutes,
        due_on=task.due_on,
    )


async def _planned_blocks(s: AsyncSession, first: date, last: date) -> list[Any]:
    """The live blocks of the published plans from `first` to `last`: (day, task_id,
    block_start, block_end), by start."""
    stmt = (
        select(_PLANS.c.day, _ITEMS.c.task_id, _ITEMS.c.block_start, _ITEMS.c.block_end)
        .join(_PLANS, _PLANS.c.id == _ITEMS.c.plan_id)
        .where(
            _PLANS.c.status == "published",
            _PLANS.c.deleted_at.is_(None),
            _PLANS.c.day.between(first, last),
            _ITEMS.c.deleted_at.is_(None),
            _ITEMS.c.removed_at.is_(None),
            _ITEMS.c.block_start.is_not(None),
        )
        .order_by(_ITEMS.c.block_start, _ITEMS.c.id)
    )
    return list((await s.execute(stmt)).all())


def _week_events(
    today: Sequence[calendar.EventOut], links: Sequence[ProjectLink]
) -> list[WeekEventOut]:
    """The project's meetings by title; other busy time without one; other free-time
    events are not the project's business and are left out."""
    shown: list[WeekEventOut] = []
    for event in today:
        matched = event_matches_project(EventDTO(attendees=tuple(event.attendees)), links)
        if matched or event.busy:
            shown.append(
                WeekEventOut(
                    title=event.title if matched else None,
                    start=event.start_at,
                    end=event.end_at,
                    busy=event.busy,
                    matched=matched,
                )
            )
    return shown


async def project_week(ctx: WorkspaceContext, monday: date, project_id: UUID) -> WeekOut:
    """Monday to Sunday of one project in the workspace timezone (P1-12, FR-2.6): each
    day's working window and free blocks (as the day calendar computes them), its events
    (`_week_events`), the project's open tasks due that day and the blocks planned that
    day (another project's without its task). `unscheduled` holds the project's open
    Human and Hybrid tasks with an estimate and no block this week. A fixed number of
    statements, whatever the number of events, tasks or blocks. Raises NotAMonday (422)
    and NotFound (404) for an unknown project."""
    if monday.weekday() != 0:
        raise NotAMonday(f"{monday.isoformat()} is not a Monday")
    zone = (await auth.get_workspace_settings(ctx)).timezone
    tz = ZoneInfo(zone)
    days = [monday + timedelta(days=n) for n in range(7)]
    week = Interval(_day_bounds(days[0], tz).start, _day_bounds(days[-1], tz).end)
    async with tenant_session(ctx) as s:
        if not await projects.project_exists(s, project_id):
            raise NotFound("projects", project_id)
        links = [
            ProjectLink(kind=link.kind, value=link.value)
            for link in await projects.project_links(s, project_id)
        ]
        hours = hours_by_weekday(await _rows(s))
        events = await calendar.events_between(ctx, week.start, week.end, session=s)
        project_tasks = await tasks.project_tasks(s, project_id)
        blocks = await _planned_blocks(s, days[0], days[-1])
        # A trashed or purged task's item stays (no foreign key): its block is not planned.
        live_ids = await tasks.live_task_ids(s, {row.task_id for row in blocks})
    blocks = [row for row in blocks if row.task_id in live_ids]
    mine = {task.id: task for task in project_tasks}
    open_tasks = [task for task in project_tasks if task.status != "done"]
    out: list[WeekDayOut] = []
    for day in days:
        bounds = _day_bounds(day, tz)
        window = working_window(day, tz, hours, replan=False)
        today = [e for e in events if e.start_at < bounds.end and e.end_at > bounds.start]
        busy = [Interval(e.start_at, e.end_at) for e in today if e.busy and e.end_at > e.start_at]
        out.append(
            WeekDayOut(
                day=day,
                window=None if window is None else WindowOut(start=window.start, end=window.end),
                free_blocks=[
                    FreeBlockOut(start=b.start, end=b.end, minutes=b.minutes)
                    for b in calendar.free_blocks(window, busy)
                ],
                events=_week_events(today, links),
                due=[_task_ref(task) for task in open_tasks if task.due_on == day],
                planned=[
                    PlannedBlockOut(
                        task_id=row.task_id if row.task_id in mine else None,
                        title=mine[row.task_id].title if row.task_id in mine else None,
                        start=row.block_start,
                        end=row.block_end,
                    )
                    for row in blocks
                    if row.day == day
                ],
            )
        )
    planned_ids = {row.task_id for row in blocks}
    unscheduled = [
        _task_ref(task)
        for task in open_tasks
        if task.status in SCHEDULABLE_STATUSES
        and task.label in {"human", "hybrid"}
        and task.estimate_minutes is not None
        and task.id not in planned_ids
    ]
    return WeekOut(monday=monday, timezone=zone, days=out, unscheduled=unscheduled)


def _plan_task(task: tasks.TaskOut) -> PlanTask:
    # A task whose label is still pending is scheduled as the human's (only AI tasks run
    # without a block).
    return PlanTask(
        task_id=task.id,
        project_id=task.project_id,
        label=task.label or "human",
        estimate_minutes=task.estimate_minutes,
        status=task.status,
        blocked=task.status == "waiting_on_human",
        due_on=task.due_on,
        priority=0,
        rollover_count=task.rollover_count,
        created_at=task.created_at,
        eligible=task.status != "done",
    )


def _item_out(row: Any, day: date) -> PlanItemOut:
    return PlanItemOut(
        plan_id=row.plan_id,
        task_id=row.task_id,
        day=day,
        position=row.position,
        reason=row.reason,
        block_start=row.block_start,
        block_end=row.block_end,
        version=row.version,
    )


def _refusal(violations: Sequence[Violation]) -> str:
    codes = [v.code for v in violations]
    return "block_not_free" if set(codes) & NOT_FREE else codes[0]


async def _day_items(s: AsyncSession, day: date) -> tuple[UUID | None, list[Any]]:
    """The day's published plan and its live items, locked for the write."""
    plan_id: UUID | None = await s.scalar(
        select(_PLANS.c.id).where(
            _PLANS.c.day == day, _PLANS.c.status == "published", _PLANS.c.deleted_at.is_(None)
        )
    )
    if plan_id is None:
        return None, []
    rows = await s.execute(
        select(_ITEMS)
        .where(_ITEMS.c.plan_id == plan_id, _ITEMS.c.deleted_at.is_(None))
        .with_for_update()
    )
    return plan_id, list(rows.all())


async def schedule_block(
    ctx: WorkspaceContext,
    day: date,
    task_id: UUID,
    body: ManualBlockIn,
    *,
    now: datetime,
    session: AsyncSession | None = None,
) -> PlanItemOut:
    """Upserts the task's item with this block in the day's published plan, creating a
    `manual` plan for the day when it has none (P1-12, FR-2.6): the Calendar view's only
    write. The block is checked with `validate_manual_block` against the day's free blocks
    and the plan's other blocks, under a per-day lock; a violation raises ProblemError 409
    `block_not_free` (outside free time or over another block) or the violation's own
    code, with `current = {free_blocks, violations}`, and writes nothing. A `version`
    that is not the item's raises StaleVersion (409). An unknown task is NotFound (404), a
    Done one 409 `ineligible_task`, a block that ends before it starts 422."""
    calendar_day = await day_calendar(ctx, day)
    free = [Interval(b.start, b.end) for b in calendar_day.free_blocks]
    async with session_for(ctx, session) as s:
        await s.execute(_WEEK_LOCK, {"key": f"plan:{ctx.workspace_id}:{day.isoformat()}"})
        # The task first: another workspace's task is 404 whatever the body says.
        task = await tasks.get_task(s, task_id)
        try:
            block = Interval(body.block_start, body.block_end)
        except ValueError:
            raise ProblemError(
                422, "validation_error", "the block must end after it starts"
            ) from None
        if task.status == "done":
            raise ProblemError(409, "ineligible_task", "a Done task cannot be scheduled")
        plan_id, items = await _day_items(s, day)
        current = next((row for row in items if row.task_id == task_id), None)
        if body.version is not None and (current is None or current.version != body.version):
            raise StaleVersion(
                current={} if current is None else _item_out(current, day).model_dump(mode="json")
            )
        others = [
            row
            for row in items
            if row.task_id != task_id and row.block_start is not None and row.removed_at is None
        ]
        # A trashed or purged task's item stays (no foreign key): its time is free again.
        live_ids = await tasks.live_task_ids(s, {row.task_id for row in others})
        planned = [
            Interval(row.block_start, row.block_end) for row in others if row.task_id in live_ids
        ]
        violations = validate_manual_block(_plan_task(task), block, free, planned, now)
        if violations:
            raise ProblemError(
                409,
                _refusal(violations),
                "That time is not free for this task.",
                current={
                    "free_blocks": [b.model_dump(mode="json") for b in calendar_day.free_blocks],
                    "violations": [v.code for v in violations],
                },
            )
        if plan_id is None:
            plan_id = (
                await s.execute(
                    _PLANS.insert()
                    .values(
                        day=day, built_at=now, source="manual", trigger="manual", status="published"
                    )
                    .returning(_PLANS.c.id)
                )
            ).scalar_one()
        stmt: Any
        if current is None:
            stmt = _ITEMS.insert().values(
                plan_id=plan_id,
                task_id=task_id,
                position=max((row.position for row in items), default=0) + 1,
                reason=MANUAL_REASON,
                block_start=block.start,
                block_end=block.end,
            )
        else:
            stmt = (
                update(_ITEMS)
                .where(_ITEMS.c.id == current.id)
                .values(block_start=block.start, block_end=block.end, removed_at=None)
            )
        row = (await s.execute(stmt.returning(*_ITEMS.c))).one()
        live.mark_changed(s, "task", task_id)
        return _item_out(row, day)


# --- The daily plan (P1-11) ------------------------------------------------------------------


async def plan_candidates(ctx: WorkspaceContext, day: date) -> list[UUID]:
    raise NotImplementedError
