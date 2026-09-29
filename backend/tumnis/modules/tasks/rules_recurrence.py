"""tasks recurrence and day-close rules: pure, `now` and `tz` passed in (P0-19).

Interfaces only until the P0-19 spec tests turn green.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo


class Preset(StrEnum):
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True, slots=True)
class RecurrenceSpec:
    preset: Preset | None
    cron: str | None  # 5 fields, evaluated in local wall time
    weekday: int | None = None  # 0 = Monday, weekly only
    month_day: int | None = None  # 1..31, monthly only; clamps to the month's last day
    due_time: time = time(9, 0)  # plan default: the FR-4.7 working-hours start


class InvalidRecurrence(ValueError):  # noqa: N818  # the plan's name
    code = "invalid_recurrence"


Trigger = Literal["done", "tick"]


def validate_spec(spec: RecurrenceSpec) -> None:
    raise NotImplementedError


def next_occurrence(spec: RecurrenceSpec, after: datetime, tz: ZoneInfo) -> datetime:
    raise NotImplementedError


def successor(  # noqa: PLR0917  # the plan's signature
    spec: RecurrenceSpec,
    latest: datetime,
    latest_done: bool,
    trigger: Trigger,
    now: datetime,
    tz: ZoneInfo,
) -> datetime | None:
    raise NotImplementedError


def next_day_close(anchor: datetime, tz: ZoneInfo) -> datetime:
    raise NotImplementedError


def day_close_due(anchor: datetime, now: datetime, tz: ZoneInfo) -> date | None:
    raise NotImplementedError
