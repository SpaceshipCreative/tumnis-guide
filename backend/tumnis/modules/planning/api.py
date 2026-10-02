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

import logging
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Final, Literal
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import Table, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import audit, deadletter, live
from tumnis.core.cache import CacheKey, CacheSpec, invalidate_on_commit, register_cache
from tumnis.core.clock import local_to_utc
from tumnis.core.errors import ProblemError
from tumnis.core.outbox import emit
from tumnis.core.settings_store import SettingSection, get_setting, register_section
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, Interval
from tumnis.core.versioning import NotFound, StaleVersion, Version
from tumnis.modules.agents import api as agents
from tumnis.modules.auth import api as auth
from tumnis.modules.calendar import api as calendar
from tumnis.modules.integrations import api as integrations
from tumnis.modules.planning.models import (
    DailyPlan,
    PlanIssue,
    PlanItem,
    PlanPin,
    UnattendedRun,
    UnattendedWindow,
    WorkingHours,
)
from tumnis.modules.planning.payloads import PlanPublishedV1
from tumnis.modules.planning.rules import (
    DEFAULT_HOURS,
    DEFAULT_PLAN_TIME,
    DEFAULT_PLAN_WEEKDAYS,
    ELIGIBLE_STATUSES,
    MOVE_LOOKAHEAD_WORKING_DAYS,
    PRIORITY_RANK,
    REFUSAL_WORDS,
    DaySummary,
    EventDTO,
    FitOffer,
    Label,
    PlanContext,
    PlannedItem,
    PlanPick,
    PlanTask,
    ProjectLink,
    Refusal,
    RunFacts,
    TaskFacts,
    TaskLite,
    TaskRef,
    Unplaceable,
    Violation,
    Window,
    assign_blocks,
    batch_release_at,
    event_matches_project,
    fallback_key,
    free_left,
    green_light,
    is_plan_due,
    next_working_start,
    too_late_to_start,
    validate_manual_block,
    window_bounds,
    working_window,
)
from tumnis.modules.planning.rules import day_summary as summarise_day
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks
from tumnis.modules.usage import api as usage

FREE_BLOCKS_CACHE: Final = "free_blocks"  # the day calendar's cache namespace
DAY_CALENDAR_TTL_S: Final = 300.0  # bounds what no invalidation reaches (event writes
# outside a sync, such as a deselected calendar, emit no calendar.synced)
_log = logging.getLogger(__name__)
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


async def _timezone(ctx: WorkspaceContext, session: AsyncSession | None) -> str:
    """The workspace's IANA zone: through the settings cache when the caller has no
    transaction, else read in the caller's. A write never waits on a second connection
    inside its own transaction: one queued behind a lock request (a TRUNCATE, a migration)
    that waits on the first would hang where Postgres cannot see the deadlock."""
    if session is None:
        return (await auth.get_workspace_settings(ctx)).timezone
    return (await auth.workspace_timezone(session, ctx.workspace_id)).timezone


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


async def day_calendar(
    ctx: WorkspaceContext, day: date, *, session: AsyncSession | None = None
) -> DayCalendarOut:
    """The day's working window, events and free blocks in the workspace timezone (a
    weekend day has no window; Re-plan is P1-11's). With the caller's `session`, computed
    in that transaction and neither read from nor written to the cache (the cached day may
    predate the transaction's own writes, and its rows may yet roll back). Otherwise from
    the cache when it holds the day, else computed in one transaction and cached."""
    if session is not None:
        return await _compute_day(ctx, day, replan=False, session=session)
    key = day_calendar_key(ctx.workspace_id, day)
    cached = await _CACHE.get(key)
    if cached is not None:
        return DayCalendarOut.model_validate_json(cached)
    token = _CACHE.token()
    out = await _compute_day(ctx, day, replan=False)
    tags = (free_blocks_tag(ctx.workspace_id), auth.workspace_settings_tag(ctx.workspace_id))
    await _CACHE.fill(key, out.model_dump_json().encode(), since=token, tags=tags)
    return out


async def _compute_day(
    ctx: WorkspaceContext, day: date, *, replan: bool, session: AsyncSession | None = None
) -> DayCalendarOut:
    """The day calendar, computed (in the caller's `session` when it passes one): `replan`
    gives a weekend day the working window a Re-plan has (never cached: the dashboard's
    strip shows the plain day)."""
    zone = await _timezone(ctx, session)
    tz = ZoneInfo(zone)
    day_start = local_to_utc(day, time(0), tz)
    day_end = local_to_utc(day + timedelta(days=1), time(0), tz)
    async with session_for(ctx, session) as s:
        hours = hours_by_weekday(await _rows(s))
        events = await calendar.events_between(ctx, day_start, day_end, session=s)
        accounts = await integrations.connection_accounts(s, {e.connection_id for e in events})
    window = working_window(day, tz, hours, replan=replan)
    busy = [Interval(e.start_at, e.end_at) for e in events if e.busy and e.end_at > e.start_at]
    return DayCalendarOut(
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
    async with session_for(ctx, session) as s:
        calendar_day = await day_calendar(ctx, day, session=s)
        free = [Interval(b.start, b.end) for b in calendar_day.free_blocks]
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


# --- The daily plan (P1-11, FR-1.2, FR-4.3, J1, J6) -------------------------------------------
#
# `build_plan` (workflows.py) gathers here (`gather_plan`), asks the master through agents,
# places and validates with the rules, then publishes here (`publish_plan`). Everything a
# person does with the published plan (accept, swap, remove, a fit offer's split or move,
# Re-plan) is a function below that the routes call in the request's transaction.
#
# Nothing re-plans on its own: a task that becomes blocked keeps its item, and the read
# (`get_plan`) flags it from the task's live status until the person presses Re-plan.

PLAN_ISSUE: Final = "plan_issue"  # the review kind of a pick with no big enough gap
PLAN_SECTION: Final = "planning"  # the workspace setting (plan time, weekdays, timeout)
PLAN_ITEM_KIND: Final = "plan_item"  # human.decided's item_kind for accept, swap, remove
MAINTENANCE_QUEUE: Final = "maintenance"  # build_plan's queue (A9, worker concurrency 1)
BUILD_PLAN: Final = "build_plan"
MAX_ALTERNATES: Final = 20
SWAP_REASON: Final = "Swapped in by you"
_ISSUES: Table = PlanIssue.__table__  # type: ignore[assignment]
_PINS: Table = PlanPin.__table__  # type: ignore[assignment]
_PLAN_LOCK: Final = _WEEK_LOCK  # one advisory lock per workspace and day: f"plan:{ws}:{day}"

PlanSource = Literal["master", "fallback", "manual"]
PlanTrigger = Literal["morning", "replan", "manual"]
PlanNotice = Literal["agent_offline", "invalid_plan"]


class PlanningSettings(BaseModel):
    """Settings > Planning: when the morning plan is built (local time, weekdays) and how
    long the master may take (plan defaults: 08:30, Monday to Friday, 90 s)."""

    model_config = ConfigDict(extra="forbid")
    plan_time: LocalTime = DEFAULT_PLAN_TIME.strftime("%H:%M")
    plan_weekdays: list[Weekday] = Field(default_factory=lambda: sorted(DEFAULT_PLAN_WEEKDAYS))
    run_timeout_s: int = Field(default=90, ge=10, le=600)


register_section(SettingSection(PLAN_SECTION, PlanningSettings))


async def planning_settings(ctx: WorkspaceContext) -> PlanningSettings:
    found = await get_setting(ctx, PLAN_SECTION, PlanningSettings)
    return PlanningSettings() if found is None else found.value


class PlanIssuePayload(BaseModel):
    """A `plan_issue` review item: the pick that did not fit and what it is offered.
    Accept takes the split, edit takes the move, reject keeps the task off today."""

    plan_id: UUID
    day: date
    title: str = Field(max_length=500)
    estimate_minutes: int | None
    reason: str = Field(max_length=200)
    split: list[int] | None
    move_to: date | None


PLAN_ISSUE_KIND: Final = tasks.ReviewKindSpec(
    kind=PLAN_ISSUE,
    owner_module="planning",
    payload_schema=PlanIssuePayload,
    actions=("accept", "edit", "reject", "snooze"),
    impact_scope="task",
)
tasks.register_review_kind(PLAN_ISSUE_KIND)


def plan_workflow_id(workspace_id: UUID, day: date, trigger: str) -> str:
    """The morning build's id is one per workspace and day (`planner_tick` enqueues it at
    most once: DBOS 3.1.0 refuses a deduplication id on a partitioned queue, and the
    workflow id is the idempotency key everywhere else in this codebase); a Re-plan's is
    new each time."""
    suffix = "morning" if trigger == "morning" else f"{trigger}:{uuid4()}"
    return f"{BUILD_PLAN}:{workspace_id}:{day.isoformat()}:{suffix}"


def plan_id_for(workflow_id: str) -> UUID:
    """The plan a build publishes: derived from its workflow id, so a replayed publish finds
    the plan it already wrote."""
    return uuid5(_PLAN_NS, workflow_id)


_PLAN_NS: Final = UUID("4f6c1d2e-8b9a-5c3d-a1e2-f3b4c5d6e7f8")


# --- Gathering ----------------------------------------------------------------------------


def _priority(task: tasks.TaskOut) -> int:
    return PRIORITY_RANK.get(task.priority, 1)


def _planned(task: tasks.TaskOut, active: set[UUID], parents: set[UUID]) -> PlanTask:
    """A task as the plan rules see it: eligible when open (Backlog, Today, In progress),
    in an active project and without open subtasks (their parts are planned instead); a
    pending label is planned as the human's (only AI work runs without a block)."""
    return PlanTask(
        task_id=task.id,
        project_id=task.project_id,
        label=task.label.value if task.label is not None else "human",
        estimate_minutes=task.estimate_minutes,
        status=task.status.value,
        blocked=task.status == "waiting_on_human",
        due_on=task.due_on,
        priority=_priority(task),
        rollover_count=task.rollover_count,
        created_at=task.created_at,
        eligible=task.status.value in ELIGIBLE_STATUSES
        and task.project_id in active
        and task.id not in parents,
    )


class _Pool(BaseModel):
    tasks: dict[UUID, tasks.TaskOut]
    planned: dict[UUID, PlanTask]
    candidates: list[UUID]  # eligible and unblocked: the day's pins first, then fallback order


async def _pool(s: AsyncSession, day: date) -> _Pool:
    found = await tasks.open_tasks(s)
    active = await projects.active_project_ids(s)
    parents = {t.parent_id for t in found if t.parent_id is not None}
    planned = {t.id: _planned(t, active, parents) for t in found}
    pins: list[UUID] = list(
        await s.scalars(
            select(_PINS.c.task_id)
            .where(_PINS.c.day == day, _PINS.c.deleted_at.is_(None))
            .order_by(_PINS.c.created_at, _PINS.c.id)
        )
    )
    open_ids = [tid for tid, t in planned.items() if t.eligible and not t.blocked]
    pinned = [tid for tid in dict.fromkeys(pins) if tid in open_ids]
    rest = sorted(
        (tid for tid in open_ids if tid not in set(pinned)),
        key=lambda tid: fallback_key(planned[tid]),
    )
    return _Pool(tasks={t.id: t for t in found}, planned=planned, candidates=pinned + rest)


async def plan_candidates(ctx: WorkspaceContext, day: date) -> list[UUID]:
    """The day's planning candidates in the request's order: the tasks pinned to the day
    (a fit offer's move) first, then the eligible, unblocked tasks in the due-date order."""
    async with tenant_session(ctx) as s:
        return (await _pool(s, day)).candidates


async def _ahead(
    ctx: WorkspaceContext, day: date, *, session: AsyncSession | None = None
) -> dict[date, list[Interval]]:
    """The free blocks of the next MOVE_LOOKAHEAD_WORKING_DAYS working days (for move
    offers); a day without a working window is not a working day."""
    ahead: dict[date, list[Interval]] = {}
    for n in range(1, 3 * MOVE_LOOKAHEAD_WORKING_DAYS):
        if len(ahead) >= MOVE_LOOKAHEAD_WORKING_DAYS:
            break
        cal = await day_calendar(ctx, day + timedelta(days=n), session=session)
        if cal.window is not None:
            ahead[day + timedelta(days=n)] = [Interval(b.start, b.end) for b in cal.free_blocks]
    return ahead


class GatheredPlan(BaseModel):
    """What `build_plan` plans with: the rules' context and the master's request."""

    context: PlanContext
    request: dict[str, Any]  # agents.api.PlanningRequest as JSON


async def gather_plan(
    ctx: WorkspaceContext, day: date, *, trigger: str, now: datetime
) -> GatheredPlan:
    """The day's free blocks (a Re-plan also on a weekend day), events, candidates and the
    free blocks of the working days ahead, and the planning request built from them."""
    replan = trigger == "replan"
    cal = await (_compute_day(ctx, day, replan=True) if replan else day_calendar(ctx, day))
    free = [Interval(b.start, b.end) for b in cal.free_blocks]
    async with tenant_session(ctx) as s:
        pool = await _pool(s, day)
    context = PlanContext(
        day=day,
        tz=cal.timezone,
        now=now,
        free_blocks=free,
        tasks=pool.planned,
        # No block may start before the build's clock, whatever the trigger: a morning
        # plan built late (the first tick after an outage, or a plan time after the hours
        # start) is floored at `now` like a Re-plan. Before the hours start it changes
        # nothing.
        replan=True,
        ahead=await _ahead(ctx, day),
    )
    request = await agents.planning_request(
        ctx,
        day=day,
        timezone=cal.timezone,
        now=now,
        window=None if cal.window is None else Interval(cal.window.start, cal.window.end),
        free_blocks=free,
        events=[(e.title, e.start, e.end) for e in cal.events if e.end > e.start],
        candidates=[pool.tasks[tid] for tid in pool.candidates],
    )
    return GatheredPlan(context=context, request=request.model_dump(mode="json"))


async def due_plan_day(ctx: WorkspaceContext, at: datetime) -> date | None:
    """The workspace's local day when its morning plan is due at `at` (`is_plan_due`): a
    plan weekday, at or after the plan time, and no morning plan built for it yet (a
    superseded one counts: Re-plan never brings the morning plan back)."""
    tz = ZoneInfo((await auth.get_workspace_settings(ctx)).timezone)
    settings = await planning_settings(ctx)
    day = at.astimezone(tz).date()
    async with tenant_session(ctx) as s:
        built = await s.scalar(
            select(_PLANS.c.id)
            .where(
                _PLANS.c.day == day, _PLANS.c.trigger == "morning", _PLANS.c.deleted_at.is_(None)
            )
            .limit(1)
        )
    return is_plan_due(
        at, tz, _time(settings.plan_time), frozenset(settings.plan_weekdays), built is not None
    )


# --- Publishing ---------------------------------------------------------------------------


class PlanDraft(BaseModel):
    """A plan ready to publish: what `build_plan` decided."""

    plan_id: UUID
    day: date
    trigger: PlanTrigger
    source: PlanSource
    notice: PlanNotice | None
    fallback_reason: str | None
    master_run_id: UUID | None
    profile_version: str | None
    built_at: datetime
    items: list[PlannedItem]
    issues: list[Unplaceable]


async def publish_plan(ctx: WorkspaceContext, draft: PlanDraft) -> UUID:
    """Publishes the draft in one transaction under the day's lock: the day's published
    plan becomes `superseded`, the new plan, its items and its issues are written (each
    issue also a `plan_issue` review item), and `plan.published` is emitted once (unless
    the plan is empty: no items and no issues). The plan sorts after the day's earlier
    plans (`built_at` is moved just past theirs when the caller's clock is behind). A
    draft whose plan already exists (a replayed step) writes nothing and returns its id."""
    async with tenant_session(ctx) as s:
        await s.execute(_PLAN_LOCK, {"key": f"plan:{ctx.workspace_id}:{draft.day.isoformat()}"})
        if await s.scalar(select(_PLANS.c.id).where(_PLANS.c.id == draft.plan_id)) is not None:
            return draft.plan_id
        # A plan supersedes the day's earlier plans, so it sorts after them even when the
        # caller's clock is behind the morning build's planned time (a re-plan before the
        # morning slot, or a test clock).
        built_at = draft.built_at
        latest = await s.scalar(
            select(func.max(_PLANS.c.built_at)).where(
                _PLANS.c.day == draft.day, _PLANS.c.deleted_at.is_(None)
            )
        )
        if latest is not None and latest >= built_at:
            built_at = latest + timedelta(microseconds=1)
        await s.execute(
            update(_PLANS)
            .where(
                _PLANS.c.day == draft.day,
                _PLANS.c.status == "published",
                _PLANS.c.deleted_at.is_(None),
            )
            .values(status="superseded")
        )
        await s.execute(
            _PLANS.insert().values(
                id=draft.plan_id,
                day=draft.day,
                built_at=built_at,
                source=draft.source,
                trigger=draft.trigger,
                status="published",
                notice=draft.notice,
                fallback_reason=draft.fallback_reason,
                master_run_id=draft.master_run_id,
                profile_version=draft.profile_version,
            )
        )
        for item in draft.items:
            await s.execute(
                _ITEMS.insert().values(
                    plan_id=draft.plan_id,
                    task_id=item.task_id,
                    position=item.position,
                    reason=item.reason,
                    block_start=None if item.block is None else item.block.start,
                    block_end=None if item.block is None else item.block.end,
                )
            )
        for issue in draft.issues:
            await _add_issue(s, draft, issue)
        # Quiet by default: an empty day (nothing to do, nothing to fix) is still planned,
        # so nothing re-plans it, but it announces nothing.
        if draft.items or draft.issues:
            await emit(
                s,
                PlanPublishedV1(
                    plan_id=draft.plan_id,
                    day=draft.day,
                    task_ids=[item.task_id for item in draft.items],
                    reasons=[item.reason for item in draft.items],
                    source=draft.source,
                    trigger=draft.trigger,
                ),
                occurred_at=built_at,
            )
        live.mark_changed(s, "plan", draft.plan_id)
    return draft.plan_id


async def _add_issue(s: AsyncSession, draft: PlanDraft, issue: Unplaceable) -> None:
    task = await tasks.get_task(s, issue.task_id)
    offer = issue.offer.model_dump(mode="json")
    issue_id = (
        await s.execute(
            _ISSUES.insert()
            .values(
                plan_id=draft.plan_id,
                task_id=issue.task_id,
                kind="no_estimate" if task.estimate_minutes is None else "no_gap",
                offer=offer,
            )
            .returning(_ISSUES.c.id)
        )
    ).scalar_one()
    item_id = await tasks.add_review_item(
        PLAN_ISSUE,
        target=tasks.TargetRef(type="task", id=issue.task_id),
        project_id=task.project_id,
        payload=PlanIssuePayload(
            plan_id=draft.plan_id,
            day=draft.day,
            title=task.title,
            estimate_minutes=task.estimate_minutes,
            reason=issue.reason,
            split=issue.offer.split,
            move_to=issue.offer.move_to,
        ).model_dump(mode="json"),
        dedupe_key=f"{PLAN_ISSUE}:{issue_id}",
        session=s,
    )
    await s.execute(update(_ISSUES).where(_ISSUES.c.id == issue_id).values(review_item_id=item_id))


# --- Reading ------------------------------------------------------------------------------


class BlockOut(BaseModel):
    start: datetime
    end: datetime


class PlanItemViewOut(BaseModel):
    """One item as the Today panel shows it: the plan's facts plus the task as it is now
    (`blocked` from its live status: a task waiting on the person stays at its position,
    flagged, until Re-plan)."""

    id: UUID
    task_id: UUID
    position: int
    reason: str
    block: BlockOut | None
    accepted_at: datetime | None
    removed_at: datetime | None  # set: removed or swapped out (hidden, kept for the record)
    swapped_from_task_id: UUID | None
    title: str
    project_id: UUID
    project_name: str
    label: str | None
    estimate_minutes: int | None
    first_action: str | None
    status: str
    blocked: bool
    version: int


class PlanIssueOut(BaseModel):
    id: UUID
    task_id: UUID
    title: str
    kind: str
    estimate_minutes: int | None
    offer: FitOffer  # split chunks and the day to move to, as offered when the plan was built
    review_item_id: UUID | None
    resolved_at: datetime | None


class PlanOut(BaseModel):
    id: UUID
    day: date
    timezone: str
    status: str
    source: str
    trigger: str
    notice: str | None
    fallback_reason: str | None
    built_at: datetime
    items: list[PlanItemViewOut]  # by position; removed ones too (removed_at set)
    issues: list[PlanIssueOut]


async def _published(s: AsyncSession, day: date) -> Any:
    row = (
        await s.execute(
            select(_PLANS).where(
                _PLANS.c.day == day, _PLANS.c.status == "published", _PLANS.c.deleted_at.is_(None)
            )
        )
    ).first()
    if row is None:
        raise ProblemError(404, "not_found", f"No plan is published for {day.isoformat()}.")
    return row


async def _plan_out(s: AsyncSession, plan: Any, zone: str) -> PlanOut:
    items = list(
        (
            await s.execute(
                select(_ITEMS)
                .where(_ITEMS.c.plan_id == plan.id, _ITEMS.c.deleted_at.is_(None))
                .order_by(_ITEMS.c.position, _ITEMS.c.removed_at.nulls_first(), _ITEMS.c.id)
            )
        ).all()
    )
    issues = list(
        (
            await s.execute(
                select(_ISSUES)
                .where(_ISSUES.c.plan_id == plan.id, _ISSUES.c.deleted_at.is_(None))
                .order_by(_ISSUES.c.created_at, _ISSUES.c.id)
            )
        ).all()
    )
    found = {
        t.id: t
        for t in await tasks.tasks_by_ids(
            s, {r.task_id for r in items} | {i.task_id for i in issues}
        )
    }
    names = await projects.project_names(s, {t.project_id for t in found.values()})
    shown = [
        PlanItemViewOut(
            id=row.id,
            task_id=row.task_id,
            position=row.position,
            reason=row.reason,
            block=None
            if row.block_start is None
            else BlockOut(start=row.block_start, end=row.block_end),
            accepted_at=row.accepted_at,
            removed_at=row.removed_at,
            swapped_from_task_id=row.swapped_from_task_id,
            title=task.title,
            project_id=task.project_id,
            project_name=names.get(task.project_id, ""),
            label=None if task.label is None else task.label.value,
            estimate_minutes=task.estimate_minutes,
            first_action=task.first_action,
            status=task.status.value,
            blocked=task.status == "waiting_on_human",
            version=row.version,
        )
        for row in items
        if (task := found.get(row.task_id)) is not None
    ]
    return PlanOut(
        id=plan.id,
        day=plan.day,
        timezone=zone,
        status=plan.status,
        source=plan.source,
        trigger=plan.trigger,
        notice=plan.notice,
        fallback_reason=plan.fallback_reason,
        built_at=plan.built_at,
        items=shown,
        issues=[
            PlanIssueOut(
                id=row.id,
                task_id=row.task_id,
                title=found[row.task_id].title,
                kind=row.kind,
                estimate_minutes=found[row.task_id].estimate_minutes,
                offer=FitOffer.model_validate(row.offer),
                review_item_id=row.review_item_id,
                resolved_at=row.resolved_at,
            )
            for row in issues
            if row.task_id in found
        ],
    )


async def get_plan(
    ctx: WorkspaceContext, day: date, *, session: AsyncSession | None = None
) -> PlanOut:
    """The day's published plan with each item's live task status; NotFound (404) when the
    day has none."""
    zone = await _timezone(ctx, session)
    async with session_for(ctx, session) as s:
        return await _plan_out(s, await _published(s, day), zone)


# --- What the person does with the plan --------------------------------------------------


async def _live_item(s: AsyncSession, plan_id: UUID, task_id: UUID) -> Any:
    row = (
        await s.execute(
            select(_ITEMS)
            .where(
                _ITEMS.c.plan_id == plan_id,
                _ITEMS.c.task_id == task_id,
                _ITEMS.c.removed_at.is_(None),
                _ITEMS.c.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).first()
    if row is None:
        raise NotFound("plan_items", task_id)
    return row


async def _lock_day(s: AsyncSession, ctx: WorkspaceContext, day: date) -> Any:
    await s.execute(_PLAN_LOCK, {"key": f"plan:{ctx.workspace_id}:{day.isoformat()}"})
    return await _published(s, day)


async def _decided(  # R-07's fields, spelled out
    s: AsyncSession,
    item_id: UUID,
    task_id: UUID,
    decision: str,
    *,
    now: datetime,
    payload: dict[str, Any] | None = None,
) -> None:
    await emit(
        s,
        tasks.HumanDecidedV1(
            item_kind=PLAN_ITEM_KIND,
            item_id=item_id,
            target_type="task",
            target_id=task_id,
            decision=decision,
            payload=payload,
        ),
        occurred_at=now,
    )


async def _accept(s: AsyncSession, ctx: WorkspaceContext, row: Any, now: datetime) -> None:
    """The task moves to Today (from Backlog; a task already further along stays), the
    item is stamped, and `human.decided` says so."""
    task = await tasks.get_task(s, row.task_id)
    if task.status == "backlog":
        await tasks.change_status(s, ctx.actor, task.id, tasks.Status.TODAY, task.version, now=now)
    if row.accepted_at is None:
        await s.execute(update(_ITEMS).where(_ITEMS.c.id == row.id).values(accepted_at=now))
    await _decided(s, row.id, row.task_id, "accept", now=now)


async def accept_item(
    ctx: WorkspaceContext, day: date, task_id: UUID, *, now: datetime, session: AsyncSession
) -> PlanOut:
    """Accept one item (J1): its task to Today through the state machine (R-10)."""
    plan = await _lock_day(session, ctx, day)
    await _accept(session, ctx, await _live_item(session, plan.id, task_id), now)
    live.mark_changed(session, "plan", plan.id)
    return await get_plan(ctx, day, session=session)


async def accept_all(
    ctx: WorkspaceContext, day: date, *, now: datetime, session: AsyncSession
) -> PlanOut:
    """Accept every live item not accepted yet (one keystroke, `A`)."""
    plan = await _lock_day(session, ctx, day)
    rows = (
        await session.execute(
            select(_ITEMS)
            .where(
                _ITEMS.c.plan_id == plan.id,
                _ITEMS.c.removed_at.is_(None),
                _ITEMS.c.accepted_at.is_(None),
                _ITEMS.c.deleted_at.is_(None),
            )
            .order_by(_ITEMS.c.position)
            .with_for_update()
        )
    ).all()
    for row in rows:
        await _accept(session, ctx, row, now)
    live.mark_changed(session, "plan", plan.id)
    return await get_plan(ctx, day, session=session)


async def remove_item(
    ctx: WorkspaceContext, day: date, task_id: UUID, *, now: datetime, session: AsyncSession
) -> PlanOut:
    """Remove one item from the day: hidden (kept with `removed_at`); the task itself is
    left as it is."""
    plan = await _lock_day(session, ctx, day)
    row = await _live_item(session, plan.id, task_id)
    await session.execute(update(_ITEMS).where(_ITEMS.c.id == row.id).values(removed_at=now))
    await _decided(session, row.id, task_id, "remove", now=now)
    live.mark_changed(session, "plan", plan.id)
    return await get_plan(ctx, day, session=session)


class SwapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    with_task_id: UUID


async def _day_free(
    ctx: WorkspaceContext, day: date, plan: Any, session: AsyncSession
) -> list[Interval]:
    cal = await (
        _compute_day(ctx, day, replan=True, session=session)
        if plan.trigger == "replan"
        else day_calendar(ctx, day, session=session)
    )
    return [Interval(b.start, b.end) for b in cal.free_blocks]


async def swap_item(  # path, body, clock and the request's transaction
    ctx: WorkspaceContext,
    day: date,
    task_id: UUID,
    body: SwapIn,
    *,
    now: datetime,
    session: AsyncSession,
) -> PlanOut:
    """Swap one item for another task at the same position (J1): the new task is placed
    with `assign_blocks` in the free time the plan's other blocks leave, no earlier than
    `now`. 409 `ineligible_task` for a task that cannot be planned or is already in the
    plan; 409 `no_gap` (with the offer in `current`) when it does not fit."""
    free = await _day_free(ctx, day, await _published(session, day), session)
    plan = await _lock_day(session, ctx, day)
    row = await _live_item(session, plan.id, task_id)
    others = (
        await session.execute(
            select(_ITEMS).where(
                _ITEMS.c.plan_id == plan.id,
                _ITEMS.c.removed_at.is_(None),
                _ITEMS.c.deleted_at.is_(None),
                _ITEMS.c.id != row.id,
            )
        )
    ).all()
    pool = await _pool(session, day)
    new = pool.planned.get(body.with_task_id)
    if (
        new is None
        or not new.eligible
        or new.blocked
        or body.with_task_id in {o.task_id for o in others}
    ):
        raise ProblemError(409, "ineligible_task", "That task cannot be planned today.")
    taken = [Interval(o.block_start, o.block_end) for o in others if o.block_start is not None]
    context = PlanContext(
        day=day,
        tz=await _timezone(ctx, session),
        now=now,
        free_blocks=free_left(free, taken),
        tasks={new.task_id: new},
        replan=True,
        ahead=await _ahead(ctx, day, session=session),
    )
    placed, unfit = assign_blocks([PlanPick(task_id=new.task_id, reason=SWAP_REASON)], context)
    if unfit:
        raise ProblemError(
            409,
            "no_gap",
            unfit[0].reason,
            current={"offer": unfit[0].offer.model_dump(mode="json")},
        )
    block = placed[0].block
    await session.execute(update(_ITEMS).where(_ITEMS.c.id == row.id).values(removed_at=now))
    values = {
        "position": row.position,
        "reason": SWAP_REASON,
        "block_start": None if block is None else block.start,
        "block_end": None if block is None else block.end,
        "removed_at": None,
        "accepted_at": None,
        "swapped_from_task_id": task_id,
    }
    existing = await session.scalar(
        select(_ITEMS.c.id).where(_ITEMS.c.plan_id == plan.id, _ITEMS.c.task_id == new.task_id)
    )
    if existing is None:
        await session.execute(
            _ITEMS.insert().values(plan_id=plan.id, task_id=new.task_id, **values)
        )
    else:
        await session.execute(update(_ITEMS).where(_ITEMS.c.id == existing).values(**values))
    await _decided(
        session, row.id, task_id, "swap", now=now, payload={"with_task_id": str(new.task_id)}
    )
    live.mark_changed(session, "plan", plan.id)
    return await get_plan(ctx, day, session=session)


class AlternateOut(BaseModel):
    id: UUID
    title: str
    project_id: UUID
    label: str | None
    estimate_minutes: int | None
    due_on: date | None


async def alternates(ctx: WorkspaceContext, day: date) -> list[AlternateOut]:
    """What a swap can bring in: the day's candidates (pins first, then the due-date
    order) not in the live plan, at most MAX_ALTERNATES."""
    async with tenant_session(ctx) as s:
        pool = await _pool(s, day)
        in_plan: set[UUID] = set(
            await s.scalars(
                select(_ITEMS.c.task_id)
                .join(_PLANS, _PLANS.c.id == _ITEMS.c.plan_id)
                .where(
                    _PLANS.c.day == day,
                    _PLANS.c.status == "published",
                    _PLANS.c.deleted_at.is_(None),
                    _ITEMS.c.removed_at.is_(None),
                    _ITEMS.c.deleted_at.is_(None),
                )
            )
        )
    out: list[AlternateOut] = []
    for tid in pool.candidates:
        if tid in in_plan:
            continue
        task = pool.tasks[tid]
        out.append(
            AlternateOut(
                id=task.id,
                title=task.title,
                project_id=task.project_id,
                label=None if task.label is None else task.label.value,
                estimate_minutes=task.estimate_minutes,
                due_on=task.due_on,
            )
        )
        if len(out) >= MAX_ALTERNATES:
            break
    return out


# --- Fit offers: split and move (J6) -----------------------------------------------------


async def _issue(s: AsyncSession, issue_id: UUID) -> Any:
    row = (
        await s.execute(
            select(_ISSUES)
            .where(_ISSUES.c.id == issue_id, _ISSUES.c.deleted_at.is_(None))
            .with_for_update()
        )
    ).first()
    if row is None:
        raise NotFound("plan_issues", issue_id)
    return row


async def _split(s: AsyncSession, ctx: WorkspaceContext, issue: Any, now: datetime) -> None:
    """Human subtasks of the offered lengths under the task, each with its first action."""
    split: list[int] | None = issue.offer.get("split")
    if not split:
        raise ProblemError(409, "no_split", "This task has no split on offer.")
    parent = await tasks.get_task(s, issue.task_id)
    for n, minutes in enumerate(split, start=1):
        suffix = f" (part {n} of {len(split)})"
        await tasks.create_task(
            s,
            ctx.actor,
            tasks.TaskCreate(
                project_id=parent.project_id,
                parent_id=parent.id,
                title=parent.title[: 500 - len(suffix)] + suffix,
                label=tasks.Label.HUMAN,
                estimate_minutes=minutes,
                first_action=parent.first_action,
            ),
            now=now,
        )


async def _move(s: AsyncSession, issue: Any) -> None:
    """A pin: the task heads that day's candidates."""
    move_to = issue.offer.get("move_to")
    if move_to is None:
        raise ProblemError(409, "no_move", "No later day has room for this task.")
    await s.execute(
        insert(_PINS)
        .values(task_id=issue.task_id, day=date.fromisoformat(str(move_to)))
        .on_conflict_do_nothing(index_elements=["workspace_id", "task_id", "day"])
    )


_EFFECTS: Final = {"accept": "split", "edit": "move", "reject": "keep_off"}


async def _resolve(  # the issue, how, who, when, and whether to decide its item
    s: AsyncSession,
    ctx: WorkspaceContext,
    issue: Any,
    action: str,
    now: datetime,
    *,
    decide: bool,
) -> None:
    if action == "accept":
        await _split(s, ctx, issue, now)
    elif action == "edit":
        await _move(s, issue)
    await s.execute(update(_ISSUES).where(_ISSUES.c.id == issue.id).values(resolved_at=now))
    if decide and issue.review_item_id is not None:
        item = await tasks.get_review_item(s, issue.review_item_id)
        if item.decided_at is None:
            await tasks.decide_review_item(
                issue.review_item_id,
                action=action,
                payload=None,
                snooze_until=None,
                version=item.version,
                actor=ctx.actor,
                now=now,
                session=s,
            )
    live.mark_changed(s, "plan", issue.plan_id)


async def resolve_issue(  # path, how, clock and the request's transaction
    ctx: WorkspaceContext,
    day: date,
    issue_id: UUID,
    how: Literal["split", "move"],
    *,
    now: datetime,
    session: AsyncSession,
) -> PlanOut:
    """Take a fit offer (J6): `split` makes Human subtasks of the offered lengths under the
    task; `move` pins the task to the offered day. Either resolves the issue and decides
    its `plan_issue` review item (accept for a split, edit for a move). 409
    `already_resolved` for an issue already resolved."""
    await session.execute(_PLAN_LOCK, {"key": f"plan:{ctx.workspace_id}:{day.isoformat()}"})
    issue = await _issue(session, issue_id)
    if issue.resolved_at is not None:
        raise ProblemError(409, "already_resolved", "This offer was already taken.")
    await _resolve(session, ctx, issue, "accept" if how == "split" else "edit", now, decide=True)
    plan = await session.scalar(select(_PLANS.c.day).where(_PLANS.c.id == issue.plan_id))
    return await get_plan(ctx, plan or day, session=session)


async def apply_issue_decision(
    ctx: WorkspaceContext, review_item_id: UUID, decision: str, *, now: datetime
) -> bool:
    """A `plan_issue` item decided in the review queue (`human.decided`): accept splits,
    edit moves, reject keeps the task off today; a snooze changes nothing. Idempotent: an
    issue already resolved (a redelivery, or the Today panel's own split or move) is left
    alone. Returns whether anything changed."""
    if decision not in _EFFECTS:
        return False
    async with tenant_session(ctx) as s:
        issue = (
            await s.execute(
                select(_ISSUES)
                .where(_ISSUES.c.review_item_id == review_item_id, _ISSUES.c.deleted_at.is_(None))
                .with_for_update()
            )
        ).first()
        if issue is None or issue.resolved_at is not None:
            return False
        await _resolve(s, ctx, issue, decision, now, decide=False)
    return True


# --- Re-plan ------------------------------------------------------------------------------


class ReplanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date | None = None  # default: today in the workspace timezone


class ReplanAccepted(BaseModel):
    day: date
    workflow_id: str


async def request_replan(ctx: WorkspaceContext, body: ReplanIn, *, now: datetime) -> ReplanAccepted:
    """Re-plan on demand (the only re-plan there is): enqueue `build_plan` with trigger
    `replan` on the maintenance queue; it supersedes the day's plan when it publishes."""
    day = body.day
    if day is None:
        zone = (await auth.get_workspace_settings(ctx)).timezone
        day = now.astimezone(ZoneInfo(zone)).date()
    workflow_id = plan_workflow_id(ctx.workspace_id, day, "replan")
    await deadletter.dbos_client().enqueue_async(
        {"queue_name": MAINTENANCE_QUEUE, "workflow_name": BUILD_PLAN, "workflow_id": workflow_id},
        str(ctx.workspace_id),
        day.isoformat(),
        "replan",
        now.isoformat(),
    )
    return ReplanAccepted(day=day, workflow_id=workflow_id)


# --- Close the day and local metrics (P1-18) ---------------------------------------------------


class DaySummaryOut(DaySummary, frozen=True):
    """`GET /v1/day/{day}/summary`: the close-the-day panel's four sections for one local
    day of the workspace. `queued_unattended` (P4-04) lists every task queued for tonight in
    queued order, with whether it will run; `queued_overnight` names the same tasks."""

    day: date
    timezone: str
    queued_unattended: list["QueuedUnattendedOut"] = Field(default_factory=list)


async def _zone(ctx: WorkspaceContext, session: AsyncSession | None = None) -> ZoneInfo:
    return ZoneInfo(await _timezone(ctx, session))


def _local_bounds(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Local midnight starting `start` to local midnight after `end`, in UTC."""
    return local_to_utc(start, time(0), tz), local_to_utc(end + timedelta(days=1), time(0), tz)


def _task_facts(facts: tasks.DayTaskFacts) -> TaskFacts:
    return TaskFacts(
        task_id=facts.task_id,
        project_id=facts.project_id,
        title=facts.title,
        label=facts.label,
        status=facts.status,
        completed_at=facts.completed_at,
        rollover_count=facts.rollover_count,
        result_posted_at=facts.result_posted_at,
    )


async def day_summary(ctx: WorkspaceContext, day: date) -> DaySummaryOut:
    """The close-the-day panel for local `day` (J7): read only; skipping it costs nothing."""
    tz = await _zone(ctx)
    start, end = _local_bounds(day, day, tz)
    async with tenant_session(ctx) as s:
        facts = await tasks.day_task_facts(s, start, end)
        runs = await agents.finished_runs(s, start, end)
        queued = await queued_unattended(s)
    summary = summarise_day(
        [_task_facts(f) for f in facts],
        [
            RunFacts(
                run_id=r.run_id,
                task_id=r.task_id,
                kind=r.kind,
                status=r.status.value,
                finished_at=r.finished_at,
            )
            for r in runs
        ],
        day,
        tz,
    )
    overnight = [
        TaskRef(task_id=q.task_id, project_id=q.project_id, title=q.title, label=q.label)
        for q in queued
    ]
    return DaySummaryOut(
        **summary.model_dump(exclude={"queued_overnight"}),
        queued_overnight=overnight,
        day=day,
        timezone=tz.key,
        queued_unattended=queued,
    )


MetricKey = Literal[
    "daily_open_rate",
    "tasks_completed_per_working_day",
    "rollover_rate",
    "estimate_error",
    "agent_share",
    "agent_acceptance_rate",
    "unattended_runs_per_week",
]


class MetricOut(BaseModel):
    """One PRD success metric: its value over the range (null while there is no data), its
    target as the PRD states it, and the phase that brings its data when it has none yet."""

    key: MetricKey
    value: float | None
    target: str
    available_after: Literal["phase 2", "phase 4"] | None


class MetricsSummaryOut(BaseModel):
    """`GET /v1/metrics/summary?from=&to=`: the success metrics over the local days `start`
    to `end`, and the phase 1 exit gate (working days planned in a row, as of today)."""

    start: date
    end: date
    plan_days_in_a_row: int
    exit_gate_days: int
    metrics: list[MetricOut]


EXIT_GATE_DAYS: Final = 10  # two working weeks planned in a row (phase 1 exit gate)
METRICS_RANGE_MAX_DAYS: Final = 366
ESTIMATED_LABELS: Final = frozenset({"human", "hybrid"})  # their estimates are human time
_TARGETS: Final[dict[MetricKey, str]] = {  # PRD, Success metrics
    "daily_open_rate": "5 of 7 days",
    "tasks_completed_per_working_day": "+50% vs a 2-week baseline",
    "rollover_rate": "under 20% of planned tasks",
    "estimate_error": "under 30%",
    "agent_share": "30% of completed tasks",
    "agent_acceptance_rate": "70% accepted without rework",
    "unattended_runs_per_week": "at least 3 per week",
}
_LATER: Final[dict[MetricKey, Literal["phase 2", "phase 4"]]] = {
    "agent_share": "phase 2",
    "agent_acceptance_rate": "phase 2",
}


async def record_app_open(ctx: WorkspaceContext, *, now: datetime, session: AsyncSession) -> None:
    """The PWA's app-start ping (P1-18): one more `app_open` on the workspace's local day,
    in the request's transaction. Nothing leaves the server."""
    tz = await _zone(ctx, session)
    await usage.record_open(session, ctx.workspace_id, now.astimezone(tz).date())


async def _plan_days(s: AsyncSession, until: date) -> dict[date, usage.PlanDayFacts]:
    """Each local day up to `until` with a plan (published, or superseded by a re-plan), and
    whether the human accepted, swapped or removed one of its items."""
    decided = func.bool_or(
        _ITEMS.c.accepted_at.is_not(None)
        | _ITEMS.c.removed_at.is_not(None)
        | _ITEMS.c.swapped_from_task_id.is_not(None)
    )
    rows = await s.execute(
        select(_PLANS.c.day, decided.label("decided"))
        .select_from(
            _PLANS.outerjoin(
                _ITEMS, (_ITEMS.c.plan_id == _PLANS.c.id) & _ITEMS.c.deleted_at.is_(None)
            )
        )
        .where(_PLANS.c.deleted_at.is_(None), _PLANS.c.day <= until)
        .group_by(_PLANS.c.day)
    )
    return {row.day: usage.PlanDayFacts(published=True, decided=bool(row.decided)) for row in rows}


async def _planned_task_ids(s: AsyncSession, start: date, end: date) -> set[UUID]:
    rows = await s.execute(
        select(_ITEMS.c.task_id)
        .join(_PLANS, _PLANS.c.id == _ITEMS.c.plan_id)
        .where(
            _PLANS.c.deleted_at.is_(None),
            _ITEMS.c.deleted_at.is_(None),
            _ITEMS.c.removed_at.is_(None),
            _PLANS.c.day.between(start, end),
        )
        .distinct()
    )
    return {row.task_id for row in rows}


async def metrics_summary(
    ctx: WorkspaceContext, start: date, end: date, *, now: datetime
) -> MetricsSummaryOut:
    """The PRD's success metrics over the local days `start` to `end`, read from the tables
    alone (P1-18: nothing is sent anywhere), and the exit gate as of today: working days in
    a row with a plan and a decision on it. Today counts once it qualifies; until then the
    run ends yesterday."""
    if (end - start).days >= METRICS_RANGE_MAX_DAYS:
        raise ProblemError(422, "invalid_range", f"at most {METRICS_RANGE_MAX_DAYS} days")
    tz = await _zone(ctx)
    weekdays = frozenset((await planning_settings(ctx)).plan_weekdays)
    today = now.astimezone(tz).date()
    lo, hi = _local_bounds(start, end, tz)
    async with tenant_session(ctx) as s:
        opened = await usage.open_days(s, start, end)
        facts = await tasks.day_task_facts(s, lo, hi)
        days = await _plan_days(s, today)
        planned = await tasks.tasks_by_ids(s, await _planned_task_ids(s, start, end))
        unattended = await unattended_runs_finished(s, lo, hi)
    done = [
        f
        for f in facts
        if f.status == "done" and f.completed_at is not None and lo <= f.completed_at < hi
    ]
    pairs = [
        (f.estimate_minutes, f.actual_minutes)
        for f in done
        if f.label in ESTIMATED_LABELS
        and f.estimate_minutes is not None
        and f.actual_minutes is not None
    ]
    gate_today = days.get(today)
    gate_end = (
        today
        if gate_today and gate_today.published and gate_today.decided
        else today - timedelta(days=1)
    )
    values: dict[MetricKey, float | None] = {
        "daily_open_rate": usage.daily_open_rate(opened, start, end),
        "tasks_completed_per_working_day": usage.tasks_completed_per_working_day(
            [f.completed_at for f in done if f.completed_at is not None], tz, start, end, weekdays
        ),
        "rollover_rate": usage.rollover_rate(
            [usage.PlannedOutcome(task_id=t.id, rollover_count=t.rollover_count) for t in planned]
        ),
        "estimate_error": usage.estimate_error(pairs),
        "unattended_runs_per_week": None
        if unattended is None
        else round(unattended * 7 / ((end - start).days + 1), 2),
    }
    return MetricsSummaryOut(
        start=start,
        end=end,
        plan_days_in_a_row=usage.consecutive_plan_days(days, gate_end, weekdays),
        exit_gate_days=EXIT_GATE_DAYS,
        metrics=[
            MetricOut(
                key=key,
                value=values.get(key),
                target=target,
                available_after=_LATER.get(key),
            )
            for key, target in _TARGETS.items()
        ],
    )


# --- Unattended run windows (P4-04, FR-4.5, SAF-1, J7) ------------------------------------
#
# The window is a setting of the workspace (`project_id` null) with a per-project override,
# kept in `unattended_windows`. Every 5 minutes `unattended_tick` (workflows) asks, per
# workspace and in its own timezone, which queued tasks sit in an open window: a task with
# a green light (`rules.green_light`) gets one run through `agents.request_run(...,
# unattended=True)` in the transaction that consumes its queue flag, so no later tick
# starts it again; any other gets one `unattended_refused` review item per night saying
# why. A task is not started when less than half the project's maximum run time is left.
# Results and refusals wait for the morning review: they carry `batch` "overnight" and the
# release time (the first working hour after the window, minus 15 minutes).

UNATTENDED_REFUSED: Final = "unattended_refused"
UNATTENDED_SECTION: Final = "unattended-window"  # the audit details' section
OVERNIGHT: Final = "overnight"
WindowSource = Literal["project", "workspace", "none"]
_WINDOWS: Table = UnattendedWindow.__table__  # type: ignore[assignment]
_UNATTENDED_RUNS: Table = UnattendedRun.__table__  # type: ignore[assignment]
# request_run's refusals (409/422) in plain words, for the refusal's review item.
_REQUEST_WORDS: Final[dict[str, str]] = {
    "agents_paused": REFUSAL_WORDS[Refusal.paused],
    "run_already_active": "Already running",
    "status_not_runnable": "Its status cannot run",
    "label_not_runnable": REFUSAL_WORDS[Refusal.not_ai],
    "no_ready_profile": "No agent ready for this project",
}


class WindowSpec(BaseModel):
    """Weekdays (0 = Monday) and local wall times in the workspace timezone; an end before
    the start crosses midnight and belongs to the weekday it starts on."""

    model_config = ConfigDict(extra="forbid")
    weekdays: Annotated[list[Weekday], Field(min_length=1, max_length=7)]
    start_local: time
    end_local: time


class UnattendedWindowIn(BaseModel):
    """The workspace's window (`project_id` null) or one project's override; `window` null
    turns it off (an override then falls back to the workspace's). `version` is the stored
    row's (null when there is none yet)."""

    model_config = ConfigDict(extra="forbid")
    project_id: UUID | None = None
    window: WindowSpec | None
    version: int | None = None


class UnattendedWindowOut(BaseModel):
    """The window in force for the workspace or a project, where it comes from, and the
    version of the row this scope stores (null when it stores none)."""

    project_id: UUID | None
    window: WindowSpec | None
    source: WindowSource
    version: int | None


class UnattendedRefusedPayload(BaseModel):
    """An `unattended_refused` review item: why a queued task did not run in the night's
    window (`refusal`, a `rules.Refusal` or request_run's refusal code, and its plain
    words), batched into the morning review. Accept takes the task off the queue."""

    refusal: str = Field(max_length=60)
    reason: str = Field(max_length=200)
    window_start: datetime
    batch: Literal["overnight"] | None = None
    release_at: datetime | None = None


async def _unattended_decided(s: AsyncSession, deciding: tasks.Deciding) -> str | None:
    """Accepting a refusal takes the task off the unattended queue."""
    if deciding.action == "accept" and deciding.target_type == "task":
        await tasks.queue_unattended(
            s, deciding.actor, deciding.target_id, queued=False, now=deciding.now
        )
    return None


tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=UNATTENDED_REFUSED,
        owner_module="planning",
        payload_schema=UnattendedRefusedPayload,
        actions=("accept", "snooze"),
        impact_scope="task",
        on_decide=_unattended_decided,
    )
)


class QueuedUnattendedOut(BaseModel):
    """A task queued for tonight, as the day close lists it: whether it will run and, when
    it will not, the refusal it would get now and its plain words."""

    task_id: UUID
    project_id: UUID
    title: str
    label: Label | None
    queued_at: datetime
    will_run: bool
    refusal: Refusal | None
    reason: str | None


def _spec(row: Any) -> WindowSpec:
    return WindowSpec(
        weekdays=sorted(row.weekdays), start_local=row.start_local, end_local=row.end_local
    )


def _window(spec: WindowSpec) -> Window:
    return Window(frozenset(spec.weekdays), spec.start_local, spec.end_local)


async def _window_rows(s: AsyncSession) -> dict[UUID | None, Any]:
    rows = (await s.execute(select(_WINDOWS).where(_WINDOWS.c.deleted_at.is_(None)))).all()
    return {row.project_id: row for row in rows}


def _effective(rows: dict[UUID | None, Any], project_id: UUID | None) -> Any:
    if project_id is not None and project_id in rows:
        return rows[project_id]
    return rows.get(None)


def _window_out(rows: dict[UUID | None, Any], project_id: UUID | None) -> UnattendedWindowOut:
    own = rows.get(project_id)
    found = _effective(rows, project_id)
    source: WindowSource = "none"
    if found is not None:
        source = "project" if found.project_id is not None else "workspace"
    return UnattendedWindowOut(
        project_id=project_id,
        window=None if found is None else _spec(found),
        source=source,
        version=None if own is None else own.version,
    )


async def get_unattended_window(
    ctx: WorkspaceContext, project_id: UUID | None = None, *, session: AsyncSession | None = None
) -> UnattendedWindowOut:
    """The window in force for the workspace, or for a project (its override, else the
    workspace's)."""
    async with session_for(ctx, session) as s:
        if project_id is not None:
            await projects.get_project(s, project_id)  # 404 for a project the caller cannot see
        return _window_out(await _window_rows(s), project_id)


async def put_unattended_window(
    ctx: WorkspaceContext,
    body: UnattendedWindowIn,
    *,
    now: datetime,
    session: AsyncSession | None = None,
) -> UnattendedWindowOut:
    """Saves (or, with `window` null, removes) the workspace's window or a project's
    override at the stored row's `version` (stale: 409 `stale_version` with the current
    window). Refuses a repeated weekday or a start equal to the end (422
    `validation_error`). Audited `settings.changed`."""
    spec = body.window
    if spec is not None and len(set(spec.weekdays)) != len(spec.weekdays):
        raise ProblemError(422, "validation_error", "each weekday appears once")
    if spec is not None and spec.start_local == spec.end_local:
        raise ProblemError(422, "validation_error", "the window starts and ends at one time")
    async with session_for(ctx, session) as s:
        if body.project_id is not None:
            await projects.get_project(s, body.project_id)
        await s.execute(_WEEK_LOCK, {"key": f"unattended_window:{ctx.workspace_id}"})
        rows = await _window_rows(s)
        own = rows.get(body.project_id)
        if (None if own is None else own.version) != body.version:
            raise StaleVersion(current=_window_out(rows, body.project_id).model_dump(mode="json"))
        values = (
            {}
            if spec is None
            else {
                "weekdays": sorted(spec.weekdays),
                "start_local": spec.start_local,
                "end_local": spec.end_local,
            }
        )
        if own is not None and spec is None:
            await s.execute(update(_WINDOWS).where(_WINDOWS.c.id == own.id).values(deleted_at=now))
        elif own is not None:
            await s.execute(update(_WINDOWS).where(_WINDOWS.c.id == own.id).values(**values))
        elif spec is not None:
            await s.execute(_WINDOWS.insert().values(project_id=body.project_id, **values))
        if own is not None or spec is not None:
            await audit.record(
                s,
                "settings.changed",
                target=("workspace", ctx.workspace_id),
                details={"section": UNATTENDED_SECTION, "fields": ["window"]},
                occurred_at=now,
                project_id=body.project_id,
            )
            live.mark_changed(s, "settings", ctx.workspace_id)
        return _window_out(await _window_rows(s), body.project_id)


def _lite(task: tasks.QueuedTask) -> TaskLite:
    return TaskLite(
        label=task.label.value if task.label is not None else None,
        status=task.status.value,
        queued=True,
        has_acceptance_criteria=task.has_acceptance_criteria,
    )


async def _refusal_now(s: AsyncSession, task: tasks.QueuedTask) -> Refusal | None:
    pause = await agents.pause_state_for(s, task.project_id)
    return green_light(
        _lite(task),
        may_run_unattended=task.may_run_unattended,
        project_paused=pause == "paused_project",
        kill_switch=pause == "paused_workspace",
    )


async def queued_unattended(s: AsyncSession) -> list[QueuedUnattendedOut]:
    """Every task queued for tonight, in queued order, with the refusal it would get now
    (the window aside: the day close shows the queue before the window opens)."""
    listed: list[QueuedUnattendedOut] = []
    for task in await tasks.unattended_queue(s):
        refusal = await _refusal_now(s, task)
        listed.append(
            QueuedUnattendedOut(
                task_id=task.task_id,
                project_id=task.project_id,
                title=task.title,
                label=task.label.value if task.label is not None else None,
                queued_at=task.queued_at,
                will_run=refusal is None,
                refusal=refusal,
                reason=None if refusal is None else REFUSAL_WORDS[refusal],
            )
        )
    return listed


DaySummaryOut.model_rebuild()


async def _result_batch(s: AsyncSession, run_id: UUID) -> tuple[str, datetime] | None:
    """agents' result hook: an unattended run's result waits for the morning review."""
    release = await s.scalar(
        select(_UNATTENDED_RUNS.c.release_at).where(
            _UNATTENDED_RUNS.c.run_id == run_id, _UNATTENDED_RUNS.c.deleted_at.is_(None)
        )
    )
    return None if release is None else (OVERNIGHT, release)


agents.register_result_batch(_result_batch)


class UnattendedTickOut(BaseModel):
    """What one tick did in one workspace."""

    started: int = 0
    refused: int = 0


async def _release_at(s: AsyncSession, window_end: datetime, tz: ZoneInfo) -> datetime:
    hours = hours_by_weekday(await _rows(s))
    return batch_release_at(window_end, next_working_start(window_end, tz, hours))


async def _refuse(  # noqa: PLR0917  # the refused task and the night's facts
    s: AsyncSession,
    task: tasks.QueuedTask,
    refusal: str,
    reason: str,
    bounds: tuple[datetime, datetime],
    release_at: datetime,
) -> None:
    """One `unattended_refused` item per task and night (its dedupe key names the window)."""
    await tasks.add_review_item(
        UNATTENDED_REFUSED,
        target=tasks.TargetRef(type="task", id=task.task_id),
        project_id=task.project_id,
        payload=UnattendedRefusedPayload(
            refusal=refusal,
            reason=reason,
            window_start=bounds[0],
            batch=OVERNIGHT,
            release_at=release_at,
        ).model_dump(mode="json"),
        dedupe_key=f"unattended:{task.task_id}:{bounds[0].isoformat()}",
        session=s,
    )


async def _start_or_refuse(  # one queued task in an open window
    ctx: WorkspaceContext,
    task: tasks.QueuedTask,
    bounds: tuple[datetime, datetime],
    tz: ZoneInfo,
    now: datetime,
) -> Literal["started", "refused", "skipped"]:
    async with tenant_session(ctx) as s:
        release_at = await _release_at(s, bounds[1], tz)
        refusal = await _refusal_now(s, task)
        if refusal is not None:
            await _refuse(s, task, refusal.value, REFUSAL_WORDS[refusal], bounds, release_at)
            return "refused"
        policy = await projects.get_policy(s, task.project_id)
        if too_late_to_start(now, bounds[1], policy.max_run_minutes):
            return "skipped"  # stays queued for the next window
        try:
            async with s.begin_nested():
                if not await tasks.consume_unattended(s, task.task_id):
                    return "skipped"  # another tick took it
                run_id = await agents.request_run(
                    task.task_id, agents.RunKind.TASK, unattended=True, ctx=ctx, session=s, now=now
                )
                await s.execute(
                    _UNATTENDED_RUNS.insert().values(
                        run_id=run_id,
                        task_id=task.task_id,
                        project_id=task.project_id,
                        window_start=bounds[0],
                        window_end=bounds[1],
                        release_at=release_at,
                    )
                )
        except ProblemError as refused:  # the flag stays: the task is still queued
            words = _REQUEST_WORDS.get(refused.code, refused.code.replace("_", " ").capitalize())
            await _refuse(s, task, refused.code, words, bounds, release_at)
            return "refused"
        return "started"


async def unattended_tick_for(ctx: WorkspaceContext, now: datetime) -> UnattendedTickOut:
    """One tick in one workspace: each queued task whose window (its project's override,
    else the workspace's) is open at `now`, in queued order, is started or refused (see
    above). Each task is its own transaction, and one task's failure never holds up the
    others."""
    tz = await _zone(ctx)
    async with tenant_session(ctx) as s:
        rows = await _window_rows(s)
        queue = await tasks.unattended_queue(s) if rows else []
    out = UnattendedTickOut()
    for task in queue:
        row = _effective(rows, task.project_id)
        bounds = None if row is None else window_bounds(_window(_spec(row)), now, tz)
        if bounds is None:
            continue
        try:
            done = await _start_or_refuse(ctx, task, bounds, tz, now)
        except Exception:  # logged; the task stays queued and the next tick tries it again
            _log.exception("unattended tick: task %s", task.task_id)
            continue
        if done == "started":
            out.started += 1
        elif done == "refused":
            out.refused += 1
    return out


async def unattended_workspaces() -> list[UUID]:
    """Every workspace with an unattended window (the tick's first step)."""
    from tumnis.core import db  # noqa: PLC0415  # the tick reads across workspaces

    async with db.app_sessionmaker()() as s, s.begin():
        workspace_ids = list(await audit.workspace_ids(s))
    found: list[UUID] = []
    for workspace_id in workspace_ids:
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
            if await s.scalar(select(_WINDOWS.c.id).where(_WINDOWS.c.deleted_at.is_(None))):
                found.append(workspace_id)
    return found


async def run_unattended_tick(now: datetime) -> int:
    """One unattended tick across every workspace, in this process (the test route's tick
    and the scheduled workflow's steps do the same work); how many runs it started."""
    started = 0
    for workspace_id in await unattended_workspaces():
        ctx = WorkspaceContext(workspace_id, SYSTEM_ACTOR)
        started += (await unattended_tick_for(ctx, now)).started
    return started


async def unattended_runs_finished(s: AsyncSession, start: datetime, end: datetime) -> int | None:
    """Unattended runs that finished successfully within [start, end); None when no run was
    ever started unattended (the metric has no data yet)."""
    if await s.scalar(select(_UNATTENDED_RUNS.c.id).limit(1)) is None:
        return None
    ours: set[UUID] = set(await s.scalars(select(_UNATTENDED_RUNS.c.run_id)))
    finished = await agents.finished_runs(s, start, end)
    return sum(1 for r in finished if r.run_id in ours and r.status.value == "succeeded")
