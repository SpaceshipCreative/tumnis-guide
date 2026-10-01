"""focus public functions and DTOs; the only file other modules may import (P2-15).

The level (FR-10.1, FR-10.9): the workspace's level is the `focus` settings section (Quiet
by default); "less of this" sets today's override (`focus_overrides`), which holds until
the local day closes. `effective_level` (rules) says which one is in force at an instant.

The routes' functions (`current`, `set_level`, `respond`, `less_of_this`) run in the
request's transaction where they write. The worker's (`start_session`, `end_sessions`,
`session_due`, `check_in`, `planned_events`, `fire_planned`, `record_activity`) run inside
the focus workflows' steps: each reads, asks the `nudge_warranted` Noul outside any
transaction when a gateable event is due (FR-11.4), then writes. Every event is written
once (`dedupe_key`) with its level and rule (FR-10.9) and emitted as `focus.event` in the
same transaction. Nothing here reads a clock: `now` is passed in.
"""

import contextlib
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Table, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.clock import local_to_utc
from tumnis.core.errors import ProblemError
from tumnis.core.live import mark_changed
from tumnis.core.outbox import emit
from tumnis.core.settings_store import SettingSection, get_setting, put_setting, register_section
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session, use_workspace
from tumnis.core.versioning import NotFound
from tumnis.modules.agents import api as agents
from tumnis.modules.auth import api as auth
from tumnis.modules.decisions import api as decisions
from tumnis.modules.focus import rules
from tumnis.modules.focus.models import FocusEvent, FocusOverride, FocusResponse, FocusSession
from tumnis.modules.focus.payloads import (
    EventKind,
    FocusEventV1,
    FocusLevelChangedV1,
    FocusRespondedV1,
    Level,
    Response,
)
from tumnis.modules.planning import api as planning
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks

__all__ = [
    "FOCUS_SECTION",
    "LIVE_ENTITY",
    "RELAY_ANSWERS",
    "RETURN_ANSWERS",
    "DetourIn",
    "DetourOut",
    "FocusCurrentOut",
    "FocusMessageOut",
    "FocusSessionOut",
    "FocusSettings",
    "GuardrailOut",
    "LessIn",
    "LevelIn",
    "PlannedOut",
    "RespondIn",
    "ReturnIn",
    "SessionStart",
    "check_in",
    "current",
    "end_sessions",
    "fire_planned",
    "less_of_this",
    "open_session_workflows",
    "planned_events",
    "prepare_next",
    "record_activity",
    "relay_reply",
    "respond",
    "return_detour",
    "session_due",
    "session_workflow_id",
    "set_level",
    "start_session",
]

_log = logging.getLogger(__name__)

FOCUS_SECTION: Final = "focus"
LIVE_ENTITY: Final = "focus"
IN_PROGRESS: Final = "in_progress"
DONE: Final = "done"
TODAY: Final = "today"
DETOUR_RULE: Final = "Guardrail · detour"  # the stored attribution of a captured detour
RECENT_RESPONSES: Final = 5

_sessions: Table = FocusSession.__table__  # type: ignore[assignment]
_events: Table = FocusEvent.__table__  # type: ignore[assignment]
_responses: Table = FocusResponse.__table__  # type: ignore[assignment]
_overrides: Table = FocusOverride.__table__  # type: ignore[assignment]


class FocusSettings(BaseModel):
    """Settings > Focus: the workspace's level (Quiet by default, FR-10.1) and whether git
    and agent activity on a task suppresses its check-ins (FR-10.7b, on by default)."""

    model_config = ConfigDict(extra="forbid")
    level: Level = "quiet"
    activity_signals: bool = True


register_section(SettingSection(FOCUS_SECTION, FocusSettings))


# --- DTOs ----------------------------------------------------------------------------------


class FocusSessionOut(BaseModel):
    id: UUID
    task_id: UUID
    title: str
    started_at: datetime
    cadence_min: int
    doubled: bool
    next_check_in_at: datetime | None  # None: no check-ins at the level in force


class FocusMessageOut(BaseModel):
    id: UUID
    kind: EventKind
    task_id: UUID | None
    level: Level
    rule: str  # 'Coach · check_in_due (25 min cadence)' (FR-10.9)
    message: str
    fired_at: datetime
    response: Response | None  # the latest answer, if any
    # P4-03 (FR-10.8): voice is on for the message's level, and its server clip if made.
    speak: bool
    clip_id: UUID | None


class GuardrailOut(BaseModel):
    """The one-task dashboard at Guardrail (P4-01, FR-10.6): the task it shows, the one it
    prepares next, and how many other tasks of today's plan are still to do."""

    current_task_id: UUID | None
    next_task_id: UUID | None
    remaining: int


class DetourOut(BaseModel):
    """A captured detour whose return question is still open (P4-01, FR-10.6)."""

    event_id: UUID
    detour_task_id: UUID
    detour_title: str
    return_to_task_id: UUID | None
    return_to_title: str | None
    message: str
    rule: str


class FocusCurrentOut(BaseModel):
    """What the focus bar shows: the level in force, the open session and today's
    messages, oldest first; at Guardrail also the one-task card's tasks, and an open
    detour's return question (P4-01)."""

    # Every answer carries every field, so the generated client types them as present.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    level: Level
    workspace_level: Level
    override_level: Level | None
    session: FocusSessionOut | None
    messages: list[FocusMessageOut]
    guardrail: GuardrailOut | None = None  # set only when the level in force is Guardrail
    detour: DetourOut | None = None


class LevelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Level


class DetourIn(BaseModel):
    """Something not in Today the person switched to, captured as a task (P4-01). The
    person picks the project (only agents skip it, J2)."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    project_id: UUID


class RespondIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    response: Response
    to_task_id: UUID | None = None  # with `switched`: the task the person moved to
    # With `switched` at Guardrail: capture it (P4-01). The pairing rules are checked in
    # `respond` (422 `invalid_detour`), after the event is found, so any body the schema
    # allows builds and an unknown event is still 404.
    detour: DetourIn | None = None


def _check_detour(body: RespondIn) -> None:
    if body.detour is None:
        return
    if body.response != "switched":
        raise ProblemError(
            422, "invalid_detour", "A detour comes only with the response 'switched'."
        )
    if body.to_task_id is not None:
        raise ProblemError(422, "invalid_detour", "A detour and to_task_id exclude each other.")


class ReturnIn(BaseModel):
    """The answer to an open detour's return question (P4-01). `version` is the detour
    task's: Return moves it back to Backlog."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["return", "stay"]
    version: int
    event_id: UUID | None = None  # the question answered, as `GET /current` shows it


class LessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID | None = None  # the message "less of this" was tapped on, if any


# --- The level -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Level:
    effective: Level
    workspace: Level
    override: Level | None
    tz: ZoneInfo
    signals: bool


async def _settings(ctx: WorkspaceContext) -> tuple[FocusSettings, int | None]:
    found = await get_setting(ctx, FOCUS_SECTION, FocusSettings)
    return (FocusSettings(), None) if found is None else (found.value, found.version)


def _day_close(now: datetime, tz: ZoneInfo) -> datetime:
    """The next local midnight after `now`: when today's override ends."""
    return local_to_utc(now.astimezone(tz).date() + timedelta(days=1), time(0), tz)


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return local_to_utc(day, time(0), tz), local_to_utc(day + timedelta(days=1), time(0), tz)


async def _level(
    s: AsyncSession, ctx: WorkspaceContext, now: datetime, settings: FocusSettings | None = None
) -> _Level:
    if settings is None:
        settings, _ = await _settings(ctx)
    tz = ZoneInfo((await auth.get_workspace_settings(ctx)).timezone)
    day = now.astimezone(tz).date()
    row = (
        await s.execute(
            select(_overrides.c.level).where(
                _overrides.c.day == day, _overrides.c.deleted_at.is_(None)
            )
        )
    ).first()
    override = None if row is None else rules.OverrideView(day, row.level)
    effective = rules.effective_level(settings.level, override, now, tz, _day_close(now, tz))
    return _Level(
        effective=effective,
        workspace=settings.level,
        override=None if override is None else override.level,
        tz=tz,
        signals=settings.activity_signals,
    )


# --- Writing events ------------------------------------------------------------------------


async def _fire(  # noqa: PLR0917  # one event's facts, spelled out
    s: AsyncSession,
    kind: EventKind,
    level: Level,
    task_id: UUID | None,
    at: datetime,
    message: str,
    dedupe_key: str,
    *,
    session_id: UUID | None = None,
    plan_id: UUID | None = None,
    detail: str | None = None,
    rule: str | None = None,
    detour_task_id: UUID | None = None,
    return_to_task_id: UUID | None = None,
) -> UUID | None:
    """Writes the event once (a repeat of `dedupe_key` writes nothing) and emits
    `focus.event` in the same transaction; the new event's id, or None. `rule` replaces
    the level-and-kind attribution (a captured detour's)."""
    rule = rule or rules.attribution(level, kind, detail)
    event_id: UUID | None = await s.scalar(
        pg_insert(_events)
        .values(
            session_id=session_id,
            task_id=task_id,
            plan_id=plan_id,
            kind=kind,
            fired_at=at,
            level=level,
            rule=rule,
            message=message,
            dedupe_key=dedupe_key,
            detour_task_id=detour_task_id,
            return_to_task_id=return_to_task_id,
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "dedupe_key"])
        .returning(_events.c.id)
    )
    if event_id is None:
        return None
    await emit(
        s,
        FocusEventV1(
            event_id=event_id,
            kind=kind,
            task_id=task_id,
            rule=rule,
            level=level,
            message=message,
            fired_at=at,
            detour_task_id=detour_task_id,
            return_to_task_id=return_to_task_id,
        ),
        occurred_at=at,
    )
    mark_changed(s, LIVE_ENTITY, event_id)
    return event_id


def _message(kind: EventKind, title: str | None, first_action: str | None) -> str:
    name = title or "the task"
    if kind == "block_start":
        step = f" First step: {first_action}" if first_action else ""
        return f"Time for {name}.{step}"
    texts: dict[str, str] = {
        "not_started": f"{name} has not started yet.",
        "check_in_due": f"Still on {name}?",
        "switched": f"Switched to {name}.",
        "stuck": f"Stuck on {name}. Name the smallest next step, or ask for help.",
        "block_end": f"The block for {name} ends now.",
        "day_end": "Working hours end now.",
    }
    return texts[kind]


async def _gate(  # the Noul's facts, spelled out
    ctx: WorkspaceContext,
    *,
    level: Level,
    kind: EventKind,
    started: datetime | None,
    now: datetime,
    task_status: str | None,
    subject: decisions.SubjectRef,
    project_id: UUID | None,
) -> Literal["fire", "suppress"]:
    """A gateable event asks the nudge_warranted Noul first (outside any transaction).
    Decisions down or nobody answering leaves the deterministic rule standing (FR-11.4).
    Only counts, kinds and minutes are sent, never the task's text."""
    if kind not in rules.GATEABLE:
        return "fire"
    async with tenant_session(ctx) as s:
        recent: list[str] = list(
            (
                await s.scalars(
                    select(_responses.c.response)
                    .where(_responses.c.deleted_at.is_(None), _responses.c.responded_at <= now)
                    .order_by(_responses.c.responded_at.desc())
                    .limit(RECENT_RESPONSES)
                )
            ).all()
        )
        last = await s.scalar(select(func.max(_events.c.fired_at)).where(_events.c.fired_at <= now))
    try:
        with use_workspace(ctx):
            answer = await decisions.ask_nudge_warranted(
                level=level,
                event_kind=kind,
                minutes_into_block=None if started is None else _minutes(now - started),
                task_status=task_status,
                recent_responses=recent,
                minutes_since_last_nudge=None if last is None else _minutes(now - last),
                subject=subject,
                project_id=project_id,
            )
    except Exception:  # Decisions failing is Decisions down (FR-11.4)
        _log.warning("focus: the nudge_warranted decision failed; the rule stands")
        answer = None
    if answer is None:
        return rules.gate(kind, None, None)
    return rules.gate(kind, rules.NoulAnswer(answer.p, answer.confidence), answer.threshold)


def _minutes(delta: timedelta) -> int:
    return max(int(delta.total_seconds() // 60), 0)


# --- Reading -------------------------------------------------------------------------------


def _state(row: Any) -> rules.SessionState:
    return rules.SessionState(
        task_id=row.task_id,
        started_at=row.started_at,
        base_cadence_min=row.cadence_min,
        streak=row.streak,
        doubled=row.doubled,
        last_check_at=row.last_check_at,
        snoozed_until=row.snoozed_until,
    )


async def _open_session(s: AsyncSession, task_id: UUID | None = None, *, lock: bool = False) -> Any:
    """The open session (of `task_id`, else the latest started), or None."""
    stmt = select(_sessions).where(_sessions.c.ended_at.is_(None), _sessions.c.deleted_at.is_(None))
    if task_id is not None:
        stmt = stmt.where(_sessions.c.task_id == task_id)
    stmt = stmt.order_by(_sessions.c.started_at.desc(), _sessions.c.id.desc()).limit(1)
    return (await s.execute(stmt.with_for_update() if lock else stmt)).first()


async def _current(
    s: AsyncSession, ctx: WorkspaceContext, now: datetime, settings: FocusSettings | None = None
) -> FocusCurrentOut:
    level = await _level(s, ctx, now, settings)
    shown: FocusSessionOut | None = None
    row = await _open_session(s)
    if row is not None:
        try:
            title = (await tasks.get_task(s, row.task_id)).title
        except NotFound:
            title = ""
        state = _state(row)
        shown = FocusSessionOut(
            id=row.id,
            task_id=row.task_id,
            title=title,
            started_at=row.started_at,
            cadence_min=int(rules.cadence(state).total_seconds() // 60),
            doubled=row.doubled,
            next_check_in_at=rules.next_check_in(state, level.effective),
        )
    start, end = _day_bounds(now.astimezone(level.tz).date(), level.tz)
    latest = (
        select(_responses.c.response)
        .where(_responses.c.event_id == _events.c.id, _responses.c.deleted_at.is_(None))
        .order_by(_responses.c.responded_at.desc(), _responses.c.created_at.desc())
        .limit(1)
        .scalar_subquery()
        .label("response")
    )
    events = (
        await s.execute(
            select(_events, latest)
            .where(
                _events.c.fired_at >= start,
                _events.c.fired_at < end,
                _events.c.deleted_at.is_(None),
            )
            .order_by(_events.c.fired_at, _events.c.created_at)
        )
    ).all()
    voice = await decisions.voice_settings(ctx)
    clips = (
        await decisions.clip_ids(ctx, [e.id for e in events], now, session=s)
        if voice.enabled_levels
        else {}
    )
    return FocusCurrentOut(
        level=level.effective,
        workspace_level=level.workspace,
        override_level=level.override,
        session=shown,
        guardrail=await _guardrail(s, ctx, now, level),
        detour=await _open_detour(s, start, end),
        messages=[
            FocusMessageOut(
                id=e.id,
                kind=e.kind,
                task_id=e.task_id,
                level=e.level,
                rule=e.rule,
                message=e.message,
                fired_at=e.fired_at,
                response=e.response,
                speak=e.level in voice.enabled_levels,
                clip_id=clips.get(e.id),
            )
            for e in events
        ],
    )


async def _title(s: AsyncSession, task_id: UUID) -> str:
    try:
        return (await tasks.get_task(s, task_id)).title
    except NotFound:
        return ""


async def _today_plan(
    s: AsyncSession, ctx: WorkspaceContext, day: date
) -> tuple[list[rules.PlanItemLite], dict[UUID, rules.TaskLite]]:
    """The day's live plan items and their tasks' statuses; empty without a plan."""
    try:
        plan = await planning.get_plan(ctx, day, session=s)
    except ProblemError:
        return [], {}
    items = [i for i in plan.items if i.removed_at is None]
    return (
        [rules.PlanItemLite(i.task_id, i.position, i.accepted_at is not None) for i in items],
        {i.task_id: rules.TaskLite(i.status) for i in items},
    )


async def _guardrail(
    s: AsyncSession, ctx: WorkspaceContext, now: datetime, level: _Level
) -> GuardrailOut | None:
    """At Guardrail: the one-task card's task, the next one, and how many others of
    today's plan are left (`rules.guardrail_tasks`)."""
    if level.effective != "guardrail":
        return None
    plan, statuses = await _today_plan(s, ctx, now.astimezone(level.tz).date())
    current = rules.current_guardrail_task(plan, statuses)
    left = rules.guardrail_tasks(plan, statuses)
    return GuardrailOut(
        current_task_id=current,
        next_task_id=rules.next_guardrail_task(plan, statuses, current),
        remaining=len([t for t in left if t != current]),
    )


def _detours() -> Any:
    """Captured detours whose return question is open, latest first."""
    return (
        select(_events)
        .where(
            _events.c.kind == "switched",
            _events.c.detour_task_id.is_not(None),
            _events.c.return_decision.is_(None),
            _events.c.deleted_at.is_(None),
        )
        .order_by(_events.c.fired_at.desc(), _events.c.created_at.desc())
    )


async def _open_detour(s: AsyncSession, start: datetime, end: datetime) -> DetourOut | None:
    """Today's latest captured detour whose return question is unanswered."""
    row = (
        await s.execute(
            _detours().where(_events.c.fired_at >= start, _events.c.fired_at < end).limit(1)
        )
    ).first()
    if row is None:
        return None
    back = row.return_to_task_id
    return DetourOut(
        event_id=row.id,
        detour_task_id=row.detour_task_id,
        detour_title=await _title(s, row.detour_task_id),
        return_to_task_id=back,
        return_to_title=None if back is None else await _title(s, back),
        message=row.message,
        rule=row.rule,
    )


async def current(
    ctx: WorkspaceContext, now: datetime, *, session: AsyncSession | None = None
) -> FocusCurrentOut:
    """`GET /v1/focus/current`: the level in force at `now`, the workspace's level and
    today's override, the open session with its next check-in, and today's messages."""
    async with session_for(ctx, session) as s:
        return await _current(s, ctx, now)


# --- The routes' writes --------------------------------------------------------------------


async def set_level(
    ctx: WorkspaceContext, body: LevelIn, *, now: datetime, session: AsyncSession
) -> FocusCurrentOut:
    """`PUT /v1/focus/level`: the workspace's level; `focus.level_changed` (scope
    `workspace`) when it changed."""
    settings, version = await _settings(ctx)
    changed = settings.model_copy(update={"level": body.level})
    await put_setting(ctx, FOCUS_SECTION, changed, expected_version=version, session=session)
    if settings.level != body.level:
        await emit(
            session,
            FocusLevelChangedV1(from_=settings.level, to=body.level, scope="workspace"),
            occurred_at=now,
        )
    mark_changed(session, LIVE_ENTITY, ctx.workspace_id)
    return await _current(session, ctx, now, changed)


async def _lower_today(s: AsyncSession, ctx: WorkspaceContext, now: datetime) -> None:
    """Today's override one level below the level in force (FR-10.9); it ends at day close."""
    level = await _level(s, ctx, now)
    lowered = rules.lower(level.effective)
    await s.execute(
        pg_insert(_overrides)
        .values(day=now.astimezone(level.tz).date(), level=lowered)
        .on_conflict_do_update(
            index_elements=["workspace_id", "day"],
            set_={"level": lowered, "updated_at": func.now(), "deleted_at": None},
        )
    )
    if lowered != level.effective:
        await emit(
            s,
            FocusLevelChangedV1(from_=level.effective, to=lowered, scope="today"),
            occurred_at=now,
        )


async def _event(s: AsyncSession, event_id: UUID) -> Any:
    row = (
        await s.execute(
            select(_events).where(_events.c.id == event_id, _events.c.deleted_at.is_(None))
        )
    ).first()
    if row is None:
        raise NotFound("focus_events", event_id)
    return row


async def _record(  # noqa: PLR0917  # the answer's facts
    s: AsyncSession,
    event: Any,
    response: Response,
    now: datetime,
    session_id: UUID | None,
    to_task_id: UUID | None = None,
) -> None:
    await s.execute(
        _responses.insert().values(
            event_id=event.id,
            task_id=event.task_id,
            response=response,
            responded_at=now,
            to_task_id=to_task_id,
        )
    )
    await emit(
        s,
        FocusRespondedV1(
            event_id=event.id, task_id=event.task_id, session_id=session_id, response=response
        ),
        occurred_at=now,
    )


async def respond(
    ctx: WorkspaceContext, body: RespondIn, *, now: datetime, session: AsyncSession
) -> FocusCurrentOut:
    """`POST /v1/focus/respond`: a one-tap answer to a focus event (FR-10.4). It moves the
    task's open session (`apply_response`: back-off, snooze), `stuck` fires a `stuck` event
    where the level fires one, and `focus.responded` is emitted; `less_of_this` is
    `less_of_this`. 404 for an unknown event."""
    s = session
    event = await _event(s, body.event_id)
    _check_detour(body)
    if body.response == "less_of_this":
        return await less_of_this(ctx, LessIn(event_id=body.event_id), now=now, session=session)
    if body.detour is not None:
        level = await _level(s, ctx, now)
        if not rules.captures_detour(level.effective):
            raise ProblemError(
                409, "detour_needs_guardrail", "A detour is captured only at Guardrail."
            )
    row = None if event.task_id is None else await _open_session(s, event.task_id, lock=True)
    if row is not None:
        moved = rules.apply_response(_state(row), body.response, now)
        await s.execute(
            update(_sessions)
            .where(_sessions.c.id == row.id)
            .values(
                streak=moved.streak,
                doubled=moved.doubled,
                last_check_at=moved.last_check_at,
                snoozed_until=moved.snoozed_until,
            )
        )
    to_task_id = body.to_task_id
    if body.detour is not None:
        to_task_id = await _capture_detour(s, ctx, event, body.detour, now)
    await _record(s, event, body.response, now, None if row is None else row.id, to_task_id)
    if body.response == "stuck":
        level = await _level(s, ctx, now)
        if rules.fires(level.effective, "stuck"):
            title = None
            if event.task_id is not None:
                title = (await tasks.get_task(s, event.task_id)).title
            await _fire(
                s,
                "stuck",
                level.effective,
                event.task_id,
                now,
                _message("stuck", title, None),
                f"stuck:{event.id}:{now.isoformat()}",
                session_id=None if row is None else row.id,
            )
    mark_changed(s, LIVE_ENTITY, event.id)
    return await _current(s, ctx, now)


async def _capture_detour(
    s: AsyncSession, ctx: WorkspaceContext, event: Any, detour: DetourIn, now: datetime
) -> UUID:
    """A switch to something not in Today, at Guardrail (P4-01, FR-10.6): the person's
    task in the project they picked (`source` detour; Jev labels it, P1-07) goes In
    progress, the task they were on goes back to Today, and `switched` fires naming both,
    with the return question. The task they were on is the open session's (else the
    event's). There is no In progress -> Today edge (R-10), so it goes through Backlog:
    the person's reset, then their plan edge, in this one transaction."""
    held = await _open_session(s)
    back_id = held.task_id if held is not None else event.task_id
    back = None
    if back_id is not None:
        with contextlib.suppress(NotFound):
            back = await tasks.get_task(s, back_id)
    if back is not None and back.status == IN_PROGRESS:
        reset = await tasks.change_status(
            s, ctx.actor, back.id, tasks.Status.BACKLOG, back.version, now=now
        )
        back = await tasks.change_status(
            s, ctx.actor, back.id, tasks.Status.TODAY, reset.version, now=now
        )
    made = await tasks.create_task(
        s,
        ctx.actor,
        tasks.TaskCreate(project_id=detour.project_id, title=detour.title),
        now=now,
        source="detour",
    )
    await tasks.change_status(
        s, ctx.actor, made.id, tasks.Status.IN_PROGRESS, made.version, now=now
    )
    message = "Captured." if back is None else f"Captured. Back to {back.title}?"
    # start_session's key for the detour's `switched`: that one then writes nothing.
    await _fire(
        s,
        "switched",
        "guardrail",
        made.id,
        now,
        message,
        f"switched:{made.id}:{now.isoformat()}",
        rule=DETOUR_RULE,
        detour_task_id=made.id,
        return_to_task_id=None if back is None else back.id,
    )
    return made.id


async def return_detour(
    ctx: WorkspaceContext, body: ReturnIn, *, now: datetime, session: AsyncSession
) -> FocusCurrentOut:
    """`POST /v1/focus/return`: the answer to the open detour's return question (P4-01).
    Return: the detour goes to Backlog (at `body.version`, else 409 `stale_version`) and
    the task to return to, when it is in Today, is In progress again. Stay: nothing
    moves. The question is answered once, and only today's, the one `GET /current` shows
    (`body.event_id` when given): 409 `no_open_detour` when none is open."""
    s = session
    level = await _level(s, ctx, now)
    start, end = _day_bounds(now.astimezone(level.tz).date(), level.tz)
    found = _detours().where(_events.c.fired_at >= start, _events.c.fired_at < end).limit(1)
    if body.event_id is not None:
        found = found.where(_events.c.id == body.event_id)
    event = (await s.execute(found.with_for_update())).first()
    if event is None:
        if body.event_id is not None:
            await _event(s, body.event_id)  # 404 for an unknown event
        raise ProblemError(409, "no_open_detour", "No detour is waiting for an answer.")
    if body.decision == "return":
        await _go_back(s, ctx, event, body.version, now)
    await s.execute(
        update(_events)
        .where(_events.c.id == event.id)
        .values(return_decision=body.decision, decided_at=now, updated_at=func.now())
    )
    mark_changed(s, LIVE_ENTITY, event.id)
    return await _current(s, ctx, now)


async def _go_back(
    s: AsyncSession, ctx: WorkspaceContext, event: Any, version: int, now: datetime
) -> None:
    detour = await tasks.get_task(s, event.detour_task_id)
    if detour.status == IN_PROGRESS:
        # Its session ends here, so going back is not counted as another switch.
        await s.execute(
            update(_sessions)
            .where(
                _sessions.c.task_id == detour.id,
                _sessions.c.ended_at.is_(None),
                _sessions.c.deleted_at.is_(None),
            )
            .values(ended_at=now, updated_at=func.now())
        )
        await tasks.change_status(s, ctx.actor, detour.id, tasks.Status.BACKLOG, version, now=now)
    if event.return_to_task_id is None:
        return
    try:
        back = await tasks.get_task(s, event.return_to_task_id)
    except NotFound:
        return
    if back.status == TODAY:
        await tasks.change_status(
            s, ctx.actor, back.id, tasks.Status.IN_PROGRESS, back.version, now=now
        )


async def less_of_this(
    ctx: WorkspaceContext, body: LessIn, *, now: datetime, session: AsyncSession
) -> FocusCurrentOut:
    """`POST /v1/focus/less`: today's level one below the level in force, until the day
    closes (`focus.level_changed`, scope `today`); on a message, the answer is recorded too
    (`focus.responded`, `less_of_this`). 404 for an unknown event."""
    s = session
    event = None if body.event_id is None else await _event(s, body.event_id)
    await _lower_today(s, ctx, now)
    if event is not None:
        row = None if event.task_id is None else await _open_session(s, event.task_id)
        await _record(s, event, "less_of_this", now, None if row is None else row.id)
    mark_changed(s, LIVE_ENTITY, ctx.workspace_id)
    return await _current(s, ctx, now)


# --- Replies the master relays from its chat channel (P2-16, FR-8.2) ------------------------

# The one-tap answers a focus message offers (FR-10.4), and a detour's return question's two.
RELAY_ANSWERS: Final[tuple[str, ...]] = ("still_on_it", "switched", "stuck", "snooze")
RETURN_ANSWERS: Final[tuple[str, ...]] = ("return", "stay")


async def relay_reply(
    ctx: WorkspaceContext, event_id: UUID, answer: str, *, now: datetime, session: AsyncSession
) -> None:
    """A reply to focus event `event_id` typed in the master's chat channel, recorded exactly
    as the person's tap in the app (`ctx` is the person): one of the four one-tap answers
    through `respond`, or `return` / `stay` to a detour's return question through
    `return_detour` at the detour task's current version. 422 `invalid_answer` for anything
    else (free text, "less of this"); 404 for an unknown event."""
    if answer in RELAY_ANSWERS:
        body = RespondIn(event_id=event_id, response=answer)
        await respond(ctx, body, now=now, session=session)
        return
    if answer not in RETURN_ANSWERS:
        raise ProblemError(
            422,
            "invalid_answer",
            "Answer with still_on_it, switched, stuck or snooze (return or stay to a detour)",
        )
    event = await _event(session, event_id)
    if event.detour_task_id is None:
        raise ProblemError(409, "no_open_detour", "This message asked no return question.")
    detour = await tasks.get_task(session, event.detour_task_id)
    decision: Literal["return", "stay"] = "return" if answer == "return" else "stay"
    await return_detour(
        ctx,
        ReturnIn(decision=decision, version=detour.version, event_id=event_id),
        now=now,
        session=session,
    )


# --- The worker: sessions ------------------------------------------------------------------


class SessionStart(BaseModel):
    """A started (or already open) session and the workflows of the sessions it ended."""

    session_id: UUID
    workflow_id: str
    started_at: datetime
    ended_workflows: list[str]


def session_workflow_id(task_id: UUID, started_at: datetime) -> str:
    return f"focus_session:{task_id}:{started_at.isoformat()}"


async def start_session(ctx: WorkspaceContext, task_id: UUID, at: datetime) -> SessionStart | None:
    """The task moved to In progress at `at`: its session (one open per task; a repeat
    returns the open one), when the level in force is Nudge or above. Another task's open
    session ends here, and `switched` fires for this task where the level fires it."""
    async with tenant_session(ctx) as s:
        level = await _level(s, ctx, at)
        if level.effective == "quiet":
            return None
        task = await tasks.get_task(s, task_id)
        if task.status != IN_PROGRESS:
            return None  # a late delivery: the task has moved on
        held = await _open_session(s, task_id, lock=True)
        if held is not None:
            return SessionStart(
                session_id=held.id,
                workflow_id=held.workflow_id,
                started_at=held.started_at,
                ended_workflows=[],
            )
        ended = list(
            (
                await s.scalars(
                    update(_sessions)
                    .where(
                        _sessions.c.ended_at.is_(None),
                        _sessions.c.deleted_at.is_(None),
                        _sessions.c.task_id != task_id,
                    )
                    .values(ended_at=at, updated_at=func.now())
                    .returning(_sessions.c.workflow_id)
                )
            ).all()
        )
        if ended and rules.fires(level.effective, "switched"):
            await _fire(
                s,
                "switched",
                level.effective,
                task_id,
                at,
                _message("switched", task.title, None),
                f"switched:{task_id}:{at.isoformat()}",
            )
        cadence = await projects.focus_cadence(s, task.project_id) or rules.DEFAULT_CADENCE_MIN
        workflow_id = session_workflow_id(task_id, at)
        made = await s.scalar(
            pg_insert(_sessions)
            .values(
                task_id=task_id,
                project_id=task.project_id,
                level=level.effective,
                cadence_min=cadence,
                started_at=at,
                streak=0,
                doubled=False,
                last_check_at=at,
                workflow_id=workflow_id,
            )
            .returning(_sessions.c.id)
        )
        mark_changed(s, LIVE_ENTITY, made)
        return SessionStart(
            session_id=made, workflow_id=workflow_id, started_at=at, ended_workflows=ended
        )


async def end_sessions(ctx: WorkspaceContext, task_id: UUID, at: datetime) -> list[str]:
    """The task left In progress: its open session ends. The workflows of its sessions
    that ended at `at`: here, or already in the request that moved it (a detour's
    Return), so those hear `end` too."""
    async with tenant_session(ctx) as s:
        await s.execute(
            update(_sessions)
            .where(
                _sessions.c.task_id == task_id,
                _sessions.c.ended_at.is_(None),
                _sessions.c.deleted_at.is_(None),
            )
            .values(ended_at=at, updated_at=func.now())
        )
        ended: list[str] = list(
            (
                await s.scalars(
                    select(_sessions.c.workflow_id).where(
                        _sessions.c.task_id == task_id,
                        _sessions.c.ended_at == at,
                        _sessions.c.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        if ended:
            mark_changed(s, LIVE_ENTITY, task_id)
        return ended


async def open_session_workflows(ctx: WorkspaceContext, task_id: UUID | None = None) -> list[str]:
    """The workflows of the open sessions (of `task_id` when given)."""
    async with tenant_session(ctx) as s:
        stmt = select(_sessions.c.workflow_id).where(
            _sessions.c.ended_at.is_(None), _sessions.c.deleted_at.is_(None)
        )
        if task_id is not None:
            stmt = stmt.where(_sessions.c.task_id == task_id)
        return list((await s.scalars(stmt)).all())


async def _session(s: AsyncSession, session_id: UUID, *, lock: bool = False) -> Any:
    stmt = select(_sessions).where(_sessions.c.id == session_id)
    return (await s.execute(stmt.with_for_update() if lock else stmt)).first()


async def session_due(ctx: WorkspaceContext, session_id: UUID, now: datetime) -> datetime | None:
    """When the session's next check-in is due at the level in force at `now` (None: none
    at this level); NotFound once the session has ended."""
    async with tenant_session(ctx) as s:
        row = await _session(s, session_id)
        if row is None or row.ended_at is not None:
            raise NotFound("focus_sessions", session_id)
        level = await _level(s, ctx, now)
        return rules.next_check_in(_state(row), level.effective)


async def check_in(ctx: WorkspaceContext, session_id: UUID, now: datetime) -> bool:
    """The session's check-in at `now`, when one is due: suppressed by git or agent activity
    since the last check (FR-10.7b) or by the Noul (FR-11.4), else fired. Either way the
    next one is a full cadence later (`last_check_at` moves to `now`). False once the
    session has ended (here too, when its task is no longer In progress)."""
    async with tenant_session(ctx) as s:
        row = await _session(s, session_id)
        if row is None or row.ended_at is not None:
            return False
        task = await tasks.get_task(s, row.task_id)
        if task.status != IN_PROGRESS:
            await s.execute(
                update(_sessions).where(_sessions.c.id == session_id).values(ended_at=now)
            )
            return False
        level = await _level(s, ctx, now)
    state = _state(row)
    due = rules.next_check_in(state, level.effective)
    if due is None or now < due:
        return True
    activity = [row.last_activity_at] if row.last_activity_at is not None else []
    activity += await agents.task_activity_times(
        ctx, row.task_id, since=state.last_check_at, until=now
    )
    quiet = rules.suppressed_by_activity(
        activity, state.last_check_at, now, signals_on=level.signals
    )
    verdict = "suppress"
    if not quiet:
        verdict = await _gate(
            ctx,
            level=level.effective,
            kind="check_in_due",
            started=row.started_at,
            now=now,
            task_status=task.status.value,
            subject=decisions.SubjectRef(type="focus_session", id=session_id),
            project_id=task.project_id,
        )
    async with tenant_session(ctx) as s:
        held = await _session(s, session_id, lock=True)
        if held is None or held.ended_at is not None:
            return False
        if (held.last_check_at, held.snoozed_until) != (row.last_check_at, row.snoozed_until):
            return True  # answered meanwhile: the next check-in is computed again
        await s.execute(
            update(_sessions)
            .where(_sessions.c.id == session_id)
            .values(last_check_at=now, snoozed_until=None, updated_at=func.now())
        )
        if verdict == "fire":
            minutes = int(rules.cadence(state).total_seconds() // 60)
            await _fire(
                s,
                "check_in_due",
                level.effective,
                row.task_id,
                now,
                _message("check_in_due", task.title, None),
                f"check_in:{session_id}:{due.isoformat()}",
                session_id=session_id,
                detail=f"{minutes} min cadence",
            )
    return True


async def record_activity(ctx: WorkspaceContext, task_ids: list[UUID], at: datetime) -> None:
    """Git or agent activity on these tasks at `at` (FR-10.7b): their open sessions'
    `last_activity_at` moves on to it."""
    if not task_ids:
        return
    async with tenant_session(ctx) as s:
        await s.execute(
            update(_sessions)
            .where(
                _sessions.c.task_id.in_(task_ids),
                _sessions.c.ended_at.is_(None),
                _sessions.c.deleted_at.is_(None),
            )
            .values(
                last_activity_at=func.greatest(func.coalesce(_sessions.c.last_activity_at, at), at)
            )
        )


async def prepare_next(ctx: WorkspaceContext, task_id: UUID, at: datetime, key: str) -> None:
    """A task went In progress at `at` (P4-01, FR-10.6): at Guardrail, the next task of
    today's plan (after the started one when it is in the plan, else after the one-task
    card's) gets its enrichment run ahead of time when it has no first action, so starting
    it costs nothing."""
    async with tenant_session(ctx) as s:
        level = await _level(s, ctx, at)
        if level.effective != "guardrail":
            return
        try:
            plan = await planning.get_plan(ctx, at.astimezone(level.tz).date(), session=s)
        except ProblemError:
            return
    items = [i for i in plan.items if i.removed_at is None]
    lite = [rules.PlanItemLite(i.task_id, i.position, i.accepted_at is not None) for i in items]
    statuses = {i.task_id: rules.TaskLite(i.status) for i in items}
    current: UUID | None = task_id
    if all(i.task_id != task_id for i in lite):
        current = rules.current_guardrail_task(lite, statuses)
    ahead = rules.next_guardrail_task(lite, statuses, current)
    item = next((i for i in items if i.task_id == ahead), None)
    if item is None or item.first_action:
        return
    await agents.enrich_ahead(ctx, item.task_id, item.project_id, key=key, now=at)


# --- The worker: the day's plan ------------------------------------------------------------


class PlannedOut(BaseModel):
    kind: EventKind
    at: datetime
    task_id: UUID | None


async def _day_end(ctx: WorkspaceContext, day: date, tz: ZoneInfo) -> datetime | None:
    """The end of the day's working hours (FR-4.7); None on a day without hours."""
    hours = await planning.get_working_hours(ctx)
    for d in hours.days:
        if d.weekday == day.weekday():
            return local_to_utc(day, time.fromisoformat(d.end), tz)
    return None


async def _plan(ctx: WorkspaceContext, plan_id: UUID, day: date) -> Any:
    """The day's published plan when it is still `plan_id`, else None (superseded)."""
    try:
        plan = await planning.get_plan(ctx, day)
    except ProblemError:
        return None
    return plan if plan.id == plan_id else None


async def planned_events(ctx: WorkspaceContext, plan_id: UUID, day: date) -> list[PlannedOut]:
    """The plan's events in time order (`rules.plan_events`): block starts, not-started
    checks and block ends of its blocked items, and the day's end of working hours."""
    plan = await _plan(ctx, plan_id, day)
    if plan is None:
        return []
    tz = ZoneInfo(plan.timezone)
    items = [
        rules.PlanItemView(i.task_id, i.block.start, i.block.end)
        for i in plan.items
        if i.block is not None and i.removed_at is None
    ]
    day_end = await _day_end(ctx, day, tz)
    # A day without hours plans no day_end: a stand-in instant, dropped below.
    found = rules.plan_events(items, day_end or local_to_utc(day + timedelta(days=1), time(0), tz))
    return [
        PlannedOut(kind=e.kind, at=e.at, task_id=e.task_id)
        for e in found
        if e.kind != "day_end" or day_end is not None
    ]


async def fire_planned(
    ctx: WorkspaceContext, plan_id: UUID, day: date, event: PlannedOut, now: datetime
) -> None:
    """A planned event came due (the workflow saw `now` reach `event.at`): fires at the
    level in force at `now` when that level fires its kind, the plan is still the day's
    and the item still in it; `not_started` only while the task is not In progress, and
    only past the Noul. Written once, at `event.at`."""
    plan = await _plan(ctx, plan_id, day)
    if plan is None:
        return
    async with tenant_session(ctx) as s:
        level = await _level(s, ctx, now)
    if not rules.fires(level.effective, event.kind):
        return
    item = None
    if event.task_id is not None:
        item = next(
            (i for i in plan.items if i.task_id == event.task_id and i.removed_at is None), None
        )
        if item is None or item.status == DONE:
            return
        if event.kind == "not_started" and not rules.not_started_holds(item.status):
            return
    verdict = await _gate(
        ctx,
        level=level.effective,
        kind=event.kind,
        started=None if item is None or item.block is None else item.block.start,
        now=now,
        task_status=None if item is None else item.status,
        subject=decisions.SubjectRef(type="task", id=event.task_id or plan_id),
        project_id=None if item is None else item.project_id,
    )
    if verdict == "suppress":
        return
    key = (
        f"day_end:{day.isoformat()}"
        if event.task_id is None
        else f"plan:{plan_id}:{event.kind}:{event.task_id}"
    )
    async with tenant_session(ctx) as s:
        await _fire(
            s,
            event.kind,
            level.effective,
            event.task_id,
            event.at,
            _message(
                event.kind,
                None if item is None else item.title,
                None if item is None else item.first_action,
            ),
            key,
            plan_id=plan_id,
        )
