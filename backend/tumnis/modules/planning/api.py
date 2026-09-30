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

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import Table, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import audit, live
from tumnis.core.cache import CacheKey, CacheSpec, invalidate_on_commit, register_cache
from tumnis.core.clock import local_to_utc
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import Interval
from tumnis.core.versioning import StaleVersion, Version
from tumnis.modules.auth import api as auth
from tumnis.modules.calendar import api as calendar
from tumnis.modules.integrations import api as integrations
from tumnis.modules.planning.models import WorkingHours
from tumnis.modules.planning.rules import DEFAULT_HOURS, working_window

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
