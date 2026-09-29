"""tasks recurrence and day-close rules: pure, `now` and `tz` passed in (P0-19, FR-3.5,
FR-3.6, REL-6). A rules-only helper of `rules.py`.

- A recurrence is a preset (daily, weekdays, weekly on a weekday, monthly on a day that
  clamps to the month's last day) or a 5-field cron, due at a local wall time. Both are
  evaluated on local dates and wall times, then converted to UTC: a time in a DST gap moves
  forward by the gap, an ambiguous one takes its first occurrence (R-12), so a rule fires
  once per local day it names, whatever the offset does.
- Cron is parsed here (numbers, `*`, ranges, steps and lists; day of week 0 or 7 is
  Sunday; a restricted day of month and day of week match either, as in Vixie cron).
  The rules may import only pure stdlib (T-P0-01-09), so neither croniter nor
  `core.clock` is imported: `_local_to_utc` is `core.clock.local_to_utc` (R-12), and
  T-P0-19-03 pins that they agree.
- `successor` decides the next instance of a rule from its latest one: on done, the next
  occurrence after the later of now and the instance's own; on a tick, the next occurrence
  after now once the instance is due and still open. Callers ask only about the latest
  instance, so an instance gets at most one successor, and missed weeks make one.
- Day close is the start of the next local day after the anchor (the later of the last
  close and the timezone change): local midnight, or the first instant of the day where DST
  skips midnight. A missed day closes once, as the local day that just ended.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Final, Literal
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

ONE_DAY: Final = timedelta(days=1)
SEARCH_DAYS: Final = 366 * 5  # a valid cron may name only 29 Feb; every spec fires within this


def _local_to_utc(day: date, local_time: time, tz: ZoneInfo) -> datetime:
    """`core.clock.local_to_utc` (R-12): fold=0 moves a gap time forward by the gap and
    takes an ambiguous time's first occurrence."""
    return datetime.combine(day, local_time.replace(tzinfo=None, fold=0), tzinfo=tz).astimezone(UTC)


# --- Cron --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Cron:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # cron numbering: 0 = Sunday
    any_day: bool  # day of month is `*`
    any_weekday: bool  # day of week is `*`

    def matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom = day.day in self.days
        dow = (day.weekday() + 1) % 7 in self.weekdays
        if self.any_day or self.any_weekday:
            return dom and dow
        return dom or dow


_FIELDS: Final = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _number(text: str, low: int, high: int) -> int:
    if not (text.isascii() and text.isdecimal()) or not low <= int(text) <= high:
        raise InvalidRecurrence(f"{text!r} is not a number from {low} to {high}")
    return int(text)


def _part(text: str, low: int, high: int) -> set[int]:
    """One comma-separated item: `*`, `n`, `a-b`, each optionally `/step`."""
    base, _, step_text = text.partition("/")
    step = _number(step_text, 1, high) if step_text else 1
    if base == "*":
        first, last = low, high
    elif "-" in base:
        first_text, _, last_text = base.partition("-")
        first, last = _number(first_text, low, high), _number(last_text, low, high)
        if first > last:
            raise InvalidRecurrence(f"{text!r} is an empty range")
    else:
        first = _number(base, low, high)
        last = high if step_text else first
    return set(range(first, last + 1, step))


def _parse_cron(expr: str) -> _Cron:
    fields = expr.split()
    if len(fields) != len(_FIELDS):
        raise InvalidRecurrence(f"cron needs 5 fields (minute hour day month weekday): {expr!r}")
    values = [
        set().union(*(_part(item, low, high) for item in field.split(",")))
        for field, (low, high) in zip(fields, _FIELDS, strict=True)
    ]
    minutes, hours, days, months, weekdays = values
    return _Cron(
        minutes=tuple(sorted(minutes)),
        hours=tuple(sorted(hours)),
        days=frozenset(days),
        months=frozenset(months),
        weekdays=frozenset(day % 7 for day in weekdays),
        any_day=fields[2].startswith("*"),
        any_weekday=fields[4].startswith("*"),
    )


# --- Specs -------------------------------------------------------------------------------------


def validate_spec(spec: RecurrenceSpec) -> None:
    """InvalidRecurrence (`invalid_recurrence`) unless the spec names exactly one of a
    preset and a 5-field cron, a weekday 0 to 6 for weekly (only), a month day 1 to 31 for
    monthly (only), and a naive due time."""
    if (spec.preset is None) == (spec.cron is None):
        raise InvalidRecurrence("Give a preset or a cron expression, not both")
    if spec.due_time.tzinfo is not None:
        raise InvalidRecurrence("The due time is a local wall time, without an offset")
    weekly, monthly = spec.preset is Preset.WEEKLY, spec.preset is Preset.MONTHLY
    if weekly != (spec.weekday is not None):
        raise InvalidRecurrence("A weekday goes with the weekly preset, and only there")
    if monthly != (spec.month_day is not None):
        raise InvalidRecurrence("A month day goes with the monthly preset, and only there")
    if spec.weekday is not None and not 0 <= spec.weekday <= 6:  # noqa: PLR2004
        raise InvalidRecurrence("The weekday is 0 (Monday) to 6 (Sunday)")
    if spec.month_day is not None and not 1 <= spec.month_day <= 31:  # noqa: PLR2004
        raise InvalidRecurrence("The month day is 1 to 31")
    if spec.cron is not None:
        _parse_cron(spec.cron)


def _last_day(day: date) -> int:
    first_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (first_next - ONE_DAY).day


def _preset_matches(spec: RecurrenceSpec, day: date) -> bool:
    match spec.preset:
        case Preset.DAILY:
            return True
        case Preset.WEEKDAYS:
            return day.weekday() < 5  # noqa: PLR2004  # Monday to Friday
        case Preset.WEEKLY:
            return day.weekday() == spec.weekday
        case _:  # monthly: the day, or the month's last day when the month is shorter
            return day.day == min(spec.month_day or 31, _last_day(day))


def next_occurrence(spec: RecurrenceSpec, after: datetime, tz: ZoneInfo) -> datetime:
    """The first occurrence strictly after `after`, in UTC. Each local date the spec names
    gives its wall times (the due time, or the cron's hours and minutes) converted with
    R-12; the earliest one after `after` wins, so a gap time that lands on a later wall time
    of the same day fires once."""
    validate_spec(spec)
    cron = _parse_cron(spec.cron) if spec.cron is not None else None
    day = after.astimezone(tz).date() - ONE_DAY
    for _ in range(SEARCH_DAYS):
        if cron is not None:
            times = (
                [time(h, m) for h in cron.hours for m in cron.minutes] if cron.matches(day) else []
            )
        else:
            times = [spec.due_time] if _preset_matches(spec, day) else []
        found = [at for t in times if (at := _local_to_utc(day, t, tz)) > after]
        if found:
            return min(found)
        day += ONE_DAY
    raise InvalidRecurrence(f"{spec.cron!r} names no date")  # e.g. 31 February


def successor(  # noqa: PLR0917  # the plan's signature
    spec: RecurrenceSpec,
    latest: datetime,
    latest_done: bool,
    trigger: Trigger,
    now: datetime,
    tz: ZoneInfo,
) -> datetime | None:
    """The occurrence of the next instance after the rule's latest one (due at `latest`),
    or None. Done: the next occurrence after the later of now and `latest` (finishing early
    never repeats the same occurrence). Tick: once `latest` is due and still open, the next
    occurrence after now (missed occurrences make one instance, not one each)."""
    if trigger == "done":
        return next_occurrence(spec, max(now, latest), tz)
    if now >= latest and not latest_done:
        return next_occurrence(spec, now, tz)
    return None


# --- Day close ---------------------------------------------------------------------------------


def next_day_close(anchor: datetime, tz: ZoneInfo) -> datetime:
    """The first local midnight after `anchor`, in UTC: the start of the next local day
    (01:00 where DST skips midnight)."""
    return _local_to_utc(anchor.astimezone(tz).date() + ONE_DAY, time(0), tz)


def day_close_due(anchor: datetime, now: datetime, tz: ZoneInfo) -> date | None:
    """The local date to close at `now`, or None before the next close after `anchor` (the
    later of the last close and the timezone change). After a gap of several days it is
    the one local day that just ended."""
    if now < next_day_close(anchor, tz):
        return None
    return now.astimezone(tz).date() - ONE_DAY
