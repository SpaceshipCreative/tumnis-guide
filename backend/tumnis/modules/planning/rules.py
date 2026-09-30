"""planning pure rules: no I/O, `now` and `tz` passed in.

- `working_window`: a day's working hours in the workspace timezone as a UTC `Interval`
  (P1-10, FR-4.7, REL-6). Hours are local wall times per weekday (0 = Monday); a weekday
  without its own hours works the default 09:00 to 18:00, a weekend day works only on
  Re-plan.
- `local_to_utc`: `core.clock.local_to_utc` (R-12). The rules may import only pure stdlib
  (T-P0-01-09), so `core.clock` is not imported here; T-P1-10-08 pins that they agree (as
  P0-19's `rules_recurrence` does).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from tumnis.core.types import Interval

DEFAULT_HOURS: Final = (time(9, 0), time(18, 0))  # FR-4.7


WEEKEND: Final = frozenset({5, 6})  # Saturday, Sunday


def working_window(
    day: date, tz: ZoneInfo, hours: Mapping[int, tuple[time, time]], *, replan: bool
) -> Interval | None:
    """Weekday with a row -> that row. Saturday or Sunday -> None, unless replan ->
    DEFAULT_HOURS (plan YAML, P1-10 red test: weekends have no window unless Re-plan).
    A weekday without a row works DEFAULT_HOURS (the workspace's hours start as the
    default), and a weekend day with a row works that row on Re-plan. Hours that a DST
    gap folds to nothing (02:30 to 03:00 on a spring-forward day) give no window."""
    weekday = day.weekday()
    if weekday in WEEKEND and not replan:
        return None
    start, end = hours.get(weekday, DEFAULT_HOURS)
    start_at, end_at = local_to_utc(day, start, tz), local_to_utc(day, end, tz)
    return Interval(start_at, end_at) if start_at < end_at else None


def local_to_utc(day: date, local_time: time, tz: ZoneInfo) -> datetime:
    """`core.clock.local_to_utc` (R-12): fold=0 moves a gap time forward by the gap and
    takes an ambiguous time's first occurrence."""
    return datetime.combine(day, local_time.replace(tzinfo=None, fold=0), tzinfo=tz).astimezone(UTC)


# --- Plans (P1-12; P1-11 extends them) --------------------------------------------------------

Label = Literal["human", "ai", "hybrid"]
TaskStatus = Literal["backlog", "today", "in_progress", "waiting_on_human", "in_review", "done"]
ViolationCode = Literal[
    "too_many_items",
    "duplicate_task",
    "unknown_task",
    "ineligible_task",
    "missing_reason",
    "reason_too_long",
    "missing_estimate",
    "missing_block",
    "ai_task_has_block",
    "block_outside_free_time",
    "block_length_mismatch",
    "blocks_overlap",
    "block_in_past",
    "exceeds_free_time",
]


class PlanTask(BaseModel, frozen=True):
    """A task as the plan rules see it (P1-11's interface)."""

    task_id: UUID
    project_id: UUID
    label: Label
    estimate_minutes: int | None
    status: TaskStatus
    blocked: bool
    due_on: date | None
    priority: int
    rollover_count: int
    created_at: datetime
    eligible: bool  # not done, not trashed, project active


class Violation(BaseModel, frozen=True):
    code: ViolationCode
    task_id: UUID | None = None


class ProjectLink(BaseModel, frozen=True):
    """A project's link as the matching rule reads it: `person` (an address) or `domain`."""

    kind: str
    value: str


class EventDTO(BaseModel, frozen=True):
    """A calendar event as the matching rule reads it: its attendees' addresses."""

    attendees: tuple[str, ...] = ()


def event_matches_project(event: EventDTO, links: Sequence[ProjectLink]) -> bool:
    """True if any attendee email equals a person link, or any attendee domain equals a
    domain link. Deterministic; Jev matching of events is not needed in v1.

    Addresses and domains compare case-insensitively and whole: a subdomain of a linked
    domain is not that domain. Other link kinds (repo, coolify_app) never match."""
    people = {link.value.strip().lower() for link in links if link.kind == "person"}
    domains = {link.value.strip().lower().lstrip("@") for link in links if link.kind == "domain"}
    for attendee in event.attendees:
        address = attendee.strip().lower()
        local, at, domain = address.rpartition("@")
        if address in people or (at and local and domain in domains):
            return True
    return False


def validate_manual_block(
    task: PlanTask,
    block: Interval,
    free: Sequence[Interval],
    planned: Sequence[Interval],
    now: datetime,
) -> list[Violation]:
    """Same codes as validate_plan for a single item (block_outside_free_time,
    blocks_overlap, block_length_mismatch, block_in_past, ai_task_has_block), in
    validate_plan's order: the estimate and the block's length, an AI task's block, the
    block inside one free block, not before `now`, and clear of the other planned blocks.
    An AI task runs anytime, so it never takes a block and nothing else is checked."""
    found: list[ViolationCode] = []
    if task.label == "ai":
        found.append("ai_task_has_block")
    else:
        if task.estimate_minutes is None:
            found.append("missing_estimate")
        elif block.end - block.start != timedelta(minutes=task.estimate_minutes):
            found.append("block_length_mismatch")
        if not any(f.start <= block.start and block.end <= f.end for f in free):
            found.append("block_outside_free_time")
        if block.start < now:
            found.append("block_in_past")
        if any(p.start < block.end and block.start < p.end for p in planned):
            found.append("blocks_overlap")
    return [Violation(code=code, task_id=task.task_id) for code in found]
