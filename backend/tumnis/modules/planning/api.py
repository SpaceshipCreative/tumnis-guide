"""planning public functions and DTOs; the only file other modules may import.

Working hours and the day calendar (P1-10, FR-4.7, FR-1.3, REL-6). Interface stubs: the
spec tests (T-P1-10-01 to 13) name these; the implementation fills them in.
"""

from datetime import date, datetime
from typing import Annotated, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tumnis.core.cache import CacheKey
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.versioning import Version

FREE_BLOCKS_CACHE: Final = "free_blocks"  # the day calendar's cache namespace


def day_calendar_key(workspace_id: UUID, day: date) -> CacheKey:
    raise NotImplementedError


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


async def get_working_hours(ctx: WorkspaceContext) -> WorkingHoursOut:
    raise NotImplementedError


async def put_working_hours(ctx: WorkspaceContext, body: WorkingHoursIn) -> WorkingHoursOut:
    raise NotImplementedError


async def day_calendar(ctx: WorkspaceContext, day: date) -> DayCalendarOut:
    raise NotImplementedError
