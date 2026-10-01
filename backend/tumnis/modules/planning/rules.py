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


# --- The daily plan (P1-11) -------------------------------------------------------------------
#
# The master decides what and why; these rules decide when, deterministically, and check the
# result against the hard constraints (FR-1.2, FR-4.3). `validate_plan` shares no helper with
# the placement (`assign_blocks`, `fallback_plan`), so a placement bug cannot hide from it.

MAX_ITEMS: Final = 5  # FR-1.2, FR-4.3
MAX_REASON: Final = 140  # plan default
SLOT_ROUNDING_MIN: Final = 5  # plan default: blocks start on 5-minute marks
MIN_SPLIT_CHUNK: Final = 15  # plan default
MOVE_LOOKAHEAD_WORKING_DAYS: Final = 5  # plan default
DEFAULT_PLAN_TIME: Final = time(8, 30)  # plan default, before FR-4.7's 09:00 start
DEFAULT_PLAN_WEEKDAYS: Final = frozenset(range(5))  # Monday to Friday
# tasks.api's priority words as the rules rank them (highest first in the fallback).
PRIORITY_RANK: Final[Mapping[str, int]] = {"urgent": 3, "high": 2, "normal": 1, "low": 0}
ELIGIBLE_STATUSES: Final = frozenset({"backlog", "today", "in_progress"})


class PlannedItem(BaseModel, frozen=True):
    """One item of a plan: the task, its place in the order, the reason shown for it and,
    for a Human or Hybrid task, its block (None for AI work, which runs anytime)."""

    task_id: UUID
    position: int
    reason: str
    block: Interval | None


class PlanPick(BaseModel, frozen=True):
    """One of the master's picks, in its order."""

    task_id: UUID
    reason: str


class PlanContext(BaseModel, frozen=True):
    """What a plan is built and checked against: the day in the workspace zone, the build's
    clock, the day's free blocks (UTC), the candidate tasks, whether this is a Re-plan (no
    block may start before `now`), and the free blocks of the working days ahead (for move
    offers)."""

    day: date
    tz: str
    now: datetime
    free_blocks: list[Interval]
    tasks: dict[UUID, PlanTask]
    replan: bool
    ahead: dict[date, list[Interval]] = {}


class FitOffer(BaseModel, frozen=True):
    """What a task with no big enough gap is offered (J6)."""

    split: list[int] | None  # chunk minutes, each >= MIN_SPLIT_CHUNK, summing to the estimate
    move_to: date | None  # first working day ahead with a block >= the estimate


class Unplaceable(BaseModel, frozen=True):
    task_id: UUID
    reason: str
    offer: FitOffer


def _reason_codes(reason: str) -> list[ViolationCode]:
    if not reason.strip():
        return ["missing_reason"]
    return ["reason_too_long"] if len(reason) > MAX_REASON else []


def _block_codes(
    block: Interval, estimate: int | None, taken: Sequence[Interval], ctx: PlanContext
) -> list[ViolationCode]:
    """A Human or Hybrid item's block against its estimate, the free time, `now` on a
    Re-plan and the blocks before it."""
    codes: list[ViolationCode] = []
    if estimate is not None and block.end - block.start != timedelta(minutes=estimate):
        codes.append("block_length_mismatch")
    if not any(f.start <= block.start and block.end <= f.end for f in ctx.free_blocks):
        codes.append("block_outside_free_time")
    if ctx.replan and block.start < ctx.now:
        codes.append("block_in_past")
    if any(p.start < block.end and block.start < p.end for p in taken):
        codes.append("blocks_overlap")
    return codes


def validate_plan(items: Sequence[PlannedItem], ctx: PlanContext) -> list[Violation]:
    """Empty list means valid. Hard constraints only; never reorders or repairs. In order:
    count at most MAX_ITEMS; no duplicate task; every task known, eligible and not blocked;
    a reason of at most MAX_REASON characters; a Human or Hybrid task has an estimate and a
    block as long as it; an AI task has no block; each block lies inside one free block,
    does not start before `now` on a Re-plan and overlaps no other block; and, as a
    summary, the Human and Hybrid minutes do not exceed the free minutes. AI work never
    counts against free time."""
    found: list[Violation] = []
    if len(items) > MAX_ITEMS:
        found.append(Violation(code="too_many_items"))
    seen: set[UUID] = set()
    taken: list[Interval] = []
    human_minutes = 0
    for item in items:
        tid = item.task_id
        task = ctx.tasks.get(tid)
        if tid in seen or task is None:
            found.append(
                Violation(code="duplicate_task" if tid in seen else "unknown_task", task_id=tid)
            )
            seen.add(tid)
            continue
        seen.add(tid)
        codes: list[ViolationCode] = []
        if not task.eligible or task.blocked:
            codes.append("ineligible_task")
        codes += _reason_codes(item.reason)
        if task.label == "ai":
            if item.block is not None:
                codes.append("ai_task_has_block")
        else:
            estimate = task.estimate_minutes
            human_minutes += estimate or 0
            if estimate is None:
                codes.append("missing_estimate")
            if item.block is not None:
                codes += _block_codes(item.block, estimate, taken, ctx)
                taken.append(item.block)
            elif estimate is not None:
                codes.append("missing_block")
        found.extend(Violation(code=code, task_id=tid) for code in codes)
    free_minutes = sum((f.end - f.start for f in ctx.free_blocks), timedelta())
    if timedelta(minutes=human_minutes) > free_minutes:
        found.append(Violation(code="exceeds_free_time"))
    return found


def check_picks(picks: Sequence[PlanPick], ctx: PlanContext) -> list[Violation]:
    """Structural check of the master's reply before placement: count, duplicates, unknown
    or ineligible tasks, reasons. Any violation rejects the whole reply (fallback): a reply
    is never repaired, or the plan would carry the master's reasons for a plan it did not
    make."""
    found: list[Violation] = []
    if len(picks) > MAX_ITEMS:
        found.append(Violation(code="too_many_items"))
    seen: set[UUID] = set()
    for pick in picks:
        tid = pick.task_id
        task = ctx.tasks.get(tid)
        if tid in seen:
            found.append(Violation(code="duplicate_task", task_id=tid))
        elif task is None:
            found.append(Violation(code="unknown_task", task_id=tid))
        elif not task.eligible or task.blocked:
            found.append(Violation(code="ineligible_task", task_id=tid))
        seen.add(tid)
        if not pick.reason.strip():
            found.append(Violation(code="missing_reason", task_id=tid))
        elif len(pick.reason) > MAX_REASON:
            found.append(Violation(code="reason_too_long", task_id=tid))
    return found


def _round_up(at: datetime) -> datetime:
    """`at` moved forward to the next SLOT_ROUNDING_MIN mark (itself when on one)."""
    step = SLOT_ROUNDING_MIN * 60
    seconds = at.timestamp()
    marks = -(-seconds // step)
    return datetime.fromtimestamp(marks * step, UTC)


class _Free:
    """The free time left while a plan is placed: segments shrink as blocks are taken."""

    def __init__(self, ctx: PlanContext) -> None:
        floor = _round_up(ctx.now) if ctx.replan else None
        segments = []
        for block in sorted(ctx.free_blocks, key=lambda b: b.start):
            start = block.start if floor is None else max(block.start, floor)
            if start < block.end:
                segments.append(Interval(start, block.end))
        self.segments = segments

    def take(self, minutes: int) -> Interval | None:
        """The earliest block of `minutes` starting on a rounding mark inside one segment,
        taken out of it; None when no segment has room."""
        length = timedelta(minutes=minutes)
        for n, seg in enumerate(self.segments):
            start = _round_up(seg.start)
            if start + length <= seg.end:
                block = Interval(start, start + length)
                rest = [
                    Interval(a, b)
                    for a, b in ((seg.start, block.start), (block.end, seg.end))
                    if a < b
                ]
                self.segments[n : n + 1] = rest
                return block
        return None


def _place(
    task: PlanTask, reason: str, position: int, free: _Free, ctx: PlanContext
) -> PlannedItem | Unplaceable:
    if task.label == "ai":
        return PlannedItem(task_id=task.task_id, position=position, reason=reason, block=None)
    if task.estimate_minutes is None:
        return Unplaceable(
            task_id=task.task_id,
            reason="No estimate yet",
            offer=FitOffer(split=None, move_to=None),
        )
    block = free.take(task.estimate_minutes)
    if block is None:
        return Unplaceable(
            task_id=task.task_id,
            reason=f"No {task.estimate_minutes}-minute gap today",
            offer=fit_offer(task, free.segments, ctx.ahead),
        )
    return PlannedItem(task_id=task.task_id, position=position, reason=reason, block=block)


def assign_blocks(
    picks: Sequence[PlanPick], ctx: PlanContext
) -> tuple[list[PlannedItem], list[Unplaceable]]:
    """In pick order: AI tasks get no block; a Human or Hybrid task takes the earliest free
    segment with room for its estimate, starting at the segment's start (or, on a Re-plan,
    no earlier than `now`) rounded up to a 5-minute mark, and the segment shrinks. A task
    that fits nowhere (or has no estimate) becomes Unplaceable with `fit_offer` against the
    time left. Picks are taken as given (`check_picks` vets them); positions run 1, 2, ...
    over the placed items."""
    free = _Free(ctx)
    items: list[PlannedItem] = []
    unplaceable: list[Unplaceable] = []
    for pick in picks:
        task = ctx.tasks[pick.task_id]
        placed = _place(task, pick.reason, len(items) + 1, free, ctx)
        if isinstance(placed, PlannedItem):
            items.append(placed)
        else:
            unplaceable.append(placed)
    return items, unplaceable


def fallback_key(task: PlanTask) -> tuple[date, int, int, datetime, UUID]:
    """The due-date order: due date (none last), priority highest first, most rolled over,
    oldest, then the id so the order is total."""
    return (
        task.due_on or date.max,
        -task.priority,
        -task.rollover_count,
        task.created_at,
        task.task_id,
    )


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"{count} times")


def fallback_reason(task: PlanTask, day: date) -> str:
    """The plain reason a due-date plan gives: the due date, the rollovers, or its age."""
    if task.due_on is not None:
        when = f"{task.due_on:%a} {task.due_on.day} {task.due_on:%b}"
        return f"Overdue since {when}" if task.due_on < day else f"Due {when}"
    if task.rollover_count > 0:
        return f"Rolled over {_times(task.rollover_count)}"
    return "Oldest open task"


def fallback_plan(ctx: PlanContext) -> tuple[list[PlannedItem], list[Unplaceable]]:
    """The due-date plan (master unreachable or its reply invalid): eligible, unblocked
    tasks in `fallback_key` order, placed like `assign_blocks`, stopping once MAX_ITEMS are
    placed. A task that does not fit is skipped; it is reported Unplaceable (with its
    offer) only when it is due on or before the day."""
    free = _Free(ctx)
    items: list[PlannedItem] = []
    unplaceable: list[Unplaceable] = []
    pool = sorted((t for t in ctx.tasks.values() if t.eligible and not t.blocked), key=fallback_key)
    for task in pool:
        if len(items) >= MAX_ITEMS:
            break
        placed = _place(task, fallback_reason(task, ctx.day), len(items) + 1, free, ctx)
        if isinstance(placed, PlannedItem):
            items.append(placed)
        elif task.due_on is not None and task.due_on <= ctx.day:
            unplaceable.append(placed)
    return items, unplaceable


def fit_offer(
    task: PlanTask, today: Sequence[Interval], ahead: Mapping[date, Sequence[Interval]]
) -> FitOffer:
    """A task with no big enough gap (J6).

    Split: today's blocks of at least MIN_SPLIT_CHUNK minutes, largest first, each filled
    with min(block, minutes left); what is still left becomes one last chunk (for another
    day). A last chunk under MIN_SPLIT_CHUNK is raised to it by taking from the chunk
    before. Offered only with at least two chunks, every chunk at least MIN_SPLIT_CHUNK and
    the leftover no larger than the largest chunk that fits a block; otherwise None.

    Move: the first day in `ahead` (in date order) with a block at least the estimate."""
    estimate = task.estimate_minutes
    if estimate is None:
        return FitOffer(split=None, move_to=None)
    chunks: list[int] = []
    left = estimate
    for minutes in sorted((b.minutes for b in today), reverse=True):
        if left <= 0:
            break
        if minutes >= MIN_SPLIT_CHUNK:
            chunks.append(min(minutes, left))
            left -= chunks[-1]
    leftover, largest = left, max(chunks, default=0)
    if leftover > 0:
        chunks.append(leftover)
    if len(chunks) >= 2 and chunks[-1] < MIN_SPLIT_CHUNK:  # noqa: PLR2004  # two chunks
        chunks[-2] -= MIN_SPLIT_CHUNK - chunks[-1]
        chunks[-1] = MIN_SPLIT_CHUNK
    fits = (
        len(chunks) >= 2  # noqa: PLR2004  # a split is two chunks or more
        and all(c >= MIN_SPLIT_CHUNK for c in chunks)
        and leftover <= largest
    )
    move_to = next(
        (day for day in sorted(ahead) if any(b.minutes >= estimate for b in ahead[day])), None
    )
    return FitOffer(split=chunks if fits else None, move_to=move_to)


def is_plan_due(
    now: datetime,
    tz: ZoneInfo,
    plan_time: time,
    plan_weekdays: frozenset[int],
    already_built: bool,  # the plan's signature
) -> date | None:
    """Local day of `now` if it is a plan weekday, no morning plan exists for it yet, and
    now is at or after local_to_utc(day, plan_time, tz). Otherwise None. A plan time a DST
    gap skips fires at the first instant after the gap (R-12); one a fall-back repeats
    fires at its first occurrence, once."""
    day = now.astimezone(tz).date()
    if already_built or day.weekday() not in plan_weekdays:
        return None
    return day if now >= local_to_utc(day, plan_time, tz) else None


def free_left(free: Sequence[Interval], taken: Sequence[Interval]) -> list[Interval]:
    """The free blocks with the taken blocks cut out, in time order (a swap places its task
    in what the plan's other blocks leave)."""
    left: list[Interval] = []
    for block in sorted(free, key=lambda b: b.start):
        pieces = [block]
        for cut in taken:
            kept: list[Interval] = []
            for piece in pieces:
                if cut.end <= piece.start or piece.end <= cut.start:
                    kept.append(piece)
                    continue
                kept += [
                    Interval(a, b)
                    for a, b in ((piece.start, cut.start), (cut.end, piece.end))
                    if a < b
                ]
            pieces = kept
        left += pieces
    return left


# --- Close the day (P1-18, J7) -----------------------------------------------------------------


class TaskRef(BaseModel, frozen=True):
    """A task as the close-the-day panel names it."""

    task_id: UUID
    project_id: UUID
    title: str
    label: Label | None


class RolloverRef(TaskRef, frozen=True):
    """A Today task that is not done: the nights it has rolled over so far, and its count
    after tonight's day close."""

    rollover_count: int
    tonight: int


class TaskFacts(BaseModel, frozen=True):
    """What `day_summary` needs of one task (read through tasks.api)."""

    task_id: UUID
    project_id: UUID
    title: str
    label: Label | None
    status: TaskStatus
    completed_at: datetime | None
    rollover_count: int
    result_posted_at: datetime | None = None  # its latest result (P2-04), if any


class RunFacts(BaseModel, frozen=True):
    """What `day_summary` needs of one agent run (read through agents.api)."""

    run_id: UUID
    task_id: UUID | None
    kind: str  # enrich, task, plan, ...
    status: str  # a RunStatus value
    finished_at: datetime | None


class DaySummary(BaseModel, frozen=True):
    shipped: list[TaskRef]  # moved to Done today (local day) by the human
    agents_finished: list[TaskRef]  # AI tasks done today, and tasks with a result posted today
    prepared_by_agents: int  # enrichment runs finished today
    queued_overnight: list[TaskRef]  # empty until P4-04
    rolls_over: list[RolloverRef]  # Today tasks not done: rollover_count now and +1 tonight


def day_summary(
    tasks: Sequence[TaskFacts], runs: Sequence[RunFacts], day: date, tz: ZoneInfo
) -> DaySummary:
    raise NotImplementedError  # P1-18
