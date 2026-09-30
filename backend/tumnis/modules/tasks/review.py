"""Review items: the only way any module queues a human decision (P0-18, R-03), and the
queue that orders and decides them (P1-13, FR-6.1, FR-1.4, R-04).

A module registers each kind it queues at startup with `register_review_kind` (a
duplicate kind raises): its owner, the payload schema, the actions the review screen
offers (R-04's accept, edit, reject, snooze, answer, approve, deny), a payload model per
action that takes one (`action_payloads`) and how far its impact reaches. New kinds need
code only, never a migration: `kind` is plain text, and the registry, not a Postgres enum,
says what is valid.

`add_review_item` validates the payload with the kind's schema, computes the item's
blocking impact (`rules.downstream` over the kind's scope) and writes the row in the
caller's transaction (its `session`, else a new one in the current workspace context),
emitting `review_item.added`; an open item with the same `dedupe_key` is returned instead
of a second one. `refresh_review_impact` recomputes the impact of a project's open items
when its tasks change; `set_review_jev` stores the factor Jev's blocking-impact decision
gives (decisions calls it; tasks never calls decisions).

`list_review_items` is the queue: open, unsnoozed items by weighted impact (blocking
impact times the Jev factor, 1.0 when none applied), then age, then id: `rules.review_key`
in SQL, by keyset. `decide_review_item` checks the action against the kind, its payload
against the action's model, and the version; it closes the item (or snoozes it until
`snooze_until`) and emits `human.decided`, whose owning module's subscriber applies the
effect. `review_badge_count` counts what waits now. Re-exported by tasks.api; other
modules import it from there.
"""

import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import Float, RowMapping, Select, Table, and_, cast, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import tenancy
from tumnis.core.clock import SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.limits import MAX_ESTIMATE_MINUTES
from tumnis.core.live import mark_changed
from tumnis.core.outbox import emit
from tumnis.core.pagination import Page, SortKey, paginate
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import ActorRef
from tumnis.core.versioning import NotFound, update_versioned
from tumnis.modules.github import api as github
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import rules
from tumnis.modules.tasks.models import ReviewItem, Task
from tumnis.modules.tasks.payloads import HumanDecidedV1, ReviewItemAddedV1

_review: Table = ReviewItem.__table__  # type: ignore[assignment]
_tasks: Table = Task.__table__  # type: ignore[assignment]

LIVE_ENTITY: Final = "review_item"  # R-05
KIND_RE: Final = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
ACTIONS: Final = frozenset({"accept", "edit", "reject", "snooze", "answer", "approve", "deny"})
ImpactScope = rules.ImpactScope
ReviewAction = rules.ReviewAction
OPEN_WHERE: Final = "decided_at IS NULL AND deleted_at IS NULL"
OPEN: Final = and_(_review.c.decided_at.is_(None), _review.c.deleted_at.is_(None))
SNOOZE: Final = "snooze"


@dataclass(frozen=True, slots=True)
class ReviewKindSpec:
    kind: str  # text slug, ^[a-z][a-z0-9_]{2,40}$; e.g. "project_match"
    owner_module: str
    payload_schema: type[BaseModel]
    actions: tuple[str, ...]  # a subset of ACTIONS (P1-13, R-04)
    impact_scope: ImpactScope
    # The payload model of each action that takes one (P1-13); an action without one takes
    # no payload.
    action_payloads: Mapping[str, type[BaseModel]] = field(
        default_factory=lambda: MappingProxyType({})
    )


class DuplicateReviewKind(ValueError):  # noqa: N818  # reads as the condition it reports
    pass


class UnknownReviewKind(ProblemError):  # noqa: N818  # the plan's name
    """No module registered this kind: 422 `unknown_review_kind`."""

    def __init__(self, kind: str) -> None:
        super().__init__(422, "unknown_review_kind", f"No review kind {kind!r} is registered")
        self.kind = kind


_KINDS: dict[str, ReviewKindSpec] = {}


def register_review_kind(spec: ReviewKindSpec) -> None:
    """Registers a kind at module startup. A kind already registered raises
    DuplicateReviewKind; a malformed slug, an unknown action or a payload model for an
    action the kind lacks raises ValueError."""
    if not KIND_RE.fullmatch(spec.kind):
        raise ValueError(f"review kind {spec.kind!r} is not a slug ^[a-z][a-z0-9_]{{2,40}}$")
    unknown = set(spec.actions) - ACTIONS
    if unknown or not spec.actions:
        raise ValueError(
            f"review kind {spec.kind!r}: actions must be a subset of {sorted(ACTIONS)}"
        )
    if set(spec.action_payloads) - set(spec.actions):
        raise ValueError(f"review kind {spec.kind!r}: a payload model names a missing action")
    existing = _KINDS.get(spec.kind)
    if existing is not None:
        raise DuplicateReviewKind(
            f"review kind {spec.kind!r} is already registered by {existing.owner_module}"
        )
    _KINDS[spec.kind] = spec


def review_kinds() -> Mapping[str, ReviewKindSpec]:
    """Every registered kind, read-only."""
    return MappingProxyType(_KINDS)


def _spec(kind: str) -> ReviewKindSpec:
    spec = _KINDS.get(kind)
    if spec is None:
        raise UnknownReviewKind(kind)
    return spec


def validate_payload(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The item payload as the `payload` column stores it: the kind's schema, dumped to
    JSON. UnknownReviewKind for a kind nobody registered; pydantic's ValidationError when
    the payload fails the schema."""
    return _spec(kind).payload_schema.model_validate(dict(payload)).model_dump(mode="json")


def validate_decision(
    spec: ReviewKindSpec,
    action: str,
    payload: Mapping[str, Any] | None,
    *,
    snooze_until: datetime | None,
    now: datetime,
) -> dict[str, Any] | None:
    """The decision's payload as `human.decided` carries it (None when the action takes
    none). 422 `action_not_allowed` for an action the kind does not offer, 422
    `invalid_snooze` for a snooze without a future `snooze_until`, 422
    `invalid_review_payload` for a payload the action's model refuses (or any payload for
    an action that takes none)."""
    if action not in spec.actions:
        raise ProblemError(
            422, "action_not_allowed", f"A {spec.kind} item cannot be decided with {action!r}"
        )
    if action == SNOOZE and (snooze_until is None or snooze_until <= now):
        raise ProblemError(422, "invalid_snooze", "A snooze needs a snooze_until in the future")
    model = spec.action_payloads.get(action)
    if model is None:
        if payload:
            raise ProblemError(422, "invalid_review_payload", f"{action!r} takes no payload")
        return None
    try:
        return model.model_validate(dict(payload or {})).model_dump(mode="json")
    except ValidationError as exc:
        raise ProblemError(
            422, "invalid_review_payload", f"The {action!r} payload is invalid: {exc}"
        ) from exc


class TargetRef(BaseModel):
    """What the decision is about: a row of some module (`type` names it)."""

    type: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    id: UUID


class ReviewItemOut(BaseModel):
    """One item as the queue shows it: its kind's actions and primary action (Enter, R-04),
    its target's title when the target is a task or a project, and its impact."""

    id: UUID
    kind: str
    project_id: UUID | None
    target_type: str
    target_id: UUID
    target_title: str | None
    payload: dict[str, Any]
    blocking_impact: float  # the deterministic impact (rules.deterministic_impact)
    jev_factor: float  # 1.0 until Jev's blocking-impact decision applied
    actions: list[str]
    primary_action: str | None  # None only for a kind no module registers any more
    snoozed_until: datetime | None
    decided_at: datetime | None
    decision: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _derived(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        row = dict(data)
        spec = _KINDS.get(str(row.get("kind")))
        row.setdefault("target_title", None)
        row.setdefault("actions", list(spec.actions) if spec is not None else [])
        row.setdefault("primary_action", rules.primary_action(spec) if spec is not None else None)
        row["blocking_impact"] = float(row.get("blocking_impact") or 0)
        row["jev_factor"] = 1.0 if row.get("jev_factor") is None else row["jev_factor"]
        return row

    @classmethod
    def from_row(cls, row: Mapping[Any, Any]) -> Self:
        return cls.model_validate(dict(row))


# --- Impact ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImpactFacts:
    """What an item blocks, as decisions' blocking-impact question reads it."""

    item_id: UUID
    kind: str
    project_id: UUID | None
    target_title: str | None
    project_name: str | None
    tasks: int
    minutes: int
    nearest_due: date | None


async def _graph(s: AsyncSession, scope: ImpactScope, project_id: UUID | None) -> rules.TaskGraph:
    """The live tasks the scope reads: the project's, or every task for "workspace"."""
    t = _tasks
    stmt = select(t.c.id, t.c.parent_id, t.c.status, t.c.label, t.c.estimate_minutes, t.c.due_on)
    stmt = stmt.where(t.c.deleted_at.is_(None))
    if scope != "workspace":
        if project_id is None:
            return rules.TaskGraph(tasks=())
        stmt = stmt.where(t.c.project_id == project_id)
    rows = (await s.execute(stmt)).all()
    return rules.TaskGraph(
        tasks=tuple(
            rules.GraphTask(
                id=row.id,
                parent_id=row.parent_id,
                status=rules.Status(row.status),
                label=None if row.label is None else rules.Label(row.label),
                estimate_minutes=row.estimate_minutes,
                due_on=row.due_on,
            )
            for row in rows
        )
    )


async def _scope_project(
    s: AsyncSession, target_type: str, target_id: UUID, project_id: UUID | None
) -> UUID | None:
    if project_id is not None or target_type != "task":
        return project_id
    found: UUID | None = await s.scalar(select(_tasks.c.project_id).where(_tasks.c.id == target_id))
    return found


def _target_task(target_type: str, target_id: UUID) -> UUID | None:
    return target_id if target_type == "task" else None


async def _impact(
    s: AsyncSession,
    spec: ReviewKindSpec,
    target_type: str,
    target_id: UUID,
    project_id: UUID | None,
    *,
    graphs: dict[UUID | None, rules.TaskGraph] | None = None,
) -> float:
    project = await _scope_project(s, target_type, target_id, project_id)
    key = None if spec.impact_scope == "workspace" else project
    cache = {} if graphs is None else graphs
    if key not in cache:
        cache[key] = await _graph(s, spec.impact_scope, project)
    tasks, minutes = rules.downstream(
        _target_task(target_type, target_id), spec.impact_scope, cache[key]
    )
    return rules.deterministic_impact(tasks, minutes)


def _impact_text(value: float) -> str:
    return repr(float(value))  # blocking_impact is text (P0-18): a float's exact repr


async def _insert(  # noqa: PLR0917  # add_review_item's fields, spelled out
    s: AsyncSession,
    spec: ReviewKindSpec,
    target: TargetRef,
    project_id: UUID | None,
    payload: dict[str, Any],
    dedupe_key: str | None,
) -> UUID:
    # A task target stores its task's project, so refresh_review_impact finds the item.
    project_id = await _scope_project(s, target.type, target.id, project_id)
    impact = await _impact(s, spec, target.type, target.id, project_id)
    stmt = pg_insert(_review).values(
        kind=spec.kind,
        project_id=project_id,
        target_type=target.type,
        target_id=target.id,
        payload=payload,
        dedupe_key=dedupe_key,
        blocking_impact=_impact_text(impact),
    )
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[_review.c.workspace_id, _review.c.dedupe_key],
            index_where=text(OPEN_WHERE),
        )
    item_id: UUID | None = await s.scalar(stmt.returning(_review.c.id))
    if item_id is None:  # an open item holds this dedupe key
        existing: UUID | None = await s.scalar(
            select(_review.c.id).where(_review.c.dedupe_key == dedupe_key, text(OPEN_WHERE))
        )
        assert existing is not None  # noqa: S101  # the conflict names a live row
        return existing
    await emit(
        s,
        ReviewItemAddedV1(
            item_id=item_id,
            kind=spec.kind,
            project_id=project_id,
            target_type=target.type,
            target_id=target.id,
        ),
        occurred_at=SystemClock().now(),
    )
    mark_changed(s, LIVE_ENTITY, item_id)
    return item_id


def _context() -> WorkspaceContext:
    ctx = tenancy.current()
    if ctx is None:
        raise RuntimeError("the review queue was called outside a workspace context")
    return ctx


@asynccontextmanager
async def _session(session: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    """The caller's transaction, else a new one in the current workspace context."""
    if session is not None:
        yield session
        return
    async with tenant_session(_context()) as own:
        yield own


async def add_review_item(
    kind: str,
    *,
    target: TargetRef,
    project_id: UUID | None,
    payload: Mapping[str, Any],
    dedupe_key: str | None = None,
    session: AsyncSession | None = None,
) -> UUID:
    """Queues a decision of a registered `kind`; returns its id. UnknownReviewKind (422
    `unknown_review_kind`) for a kind nobody registered; pydantic's ValidationError when the
    payload fails the kind's schema. Runs in `session` when given (the caller's
    transaction), else in a new transaction of the current workspace context."""
    spec = _spec(kind)
    body = validate_payload(kind, payload)
    async with _session(session) as s:
        return await _insert(s, spec, target, project_id, body, dedupe_key)


async def refresh_review_impact(s: AsyncSession, project_id: UUID) -> int:
    """Recomputes the blocking impact of the open items the project's tasks feed (its own
    items and every workspace-scoped one); returns how many changed. An unchanged value is
    not written, so the item's version holds."""
    workspace_kinds = [spec.kind for spec in _KINDS.values() if spec.impact_scope == "workspace"]
    r = _review.c
    stmt = select(r.id, r.kind, r.target_type, r.target_id, r.project_id, r.blocking_impact)
    stmt = stmt.where(
        text(OPEN_WHERE), or_(r.project_id == project_id, r.kind.in_(workspace_kinds))
    )
    rows = (await s.execute(stmt)).all()
    graphs: dict[UUID | None, rules.TaskGraph] = {}
    changed = 0
    for row in rows:
        spec = _KINDS.get(row.kind)
        if spec is None:
            continue
        impact = _impact_text(
            await _impact(s, spec, row.target_type, row.target_id, row.project_id, graphs=graphs)
        )
        if impact == row.blocking_impact:
            continue
        await s.execute(
            update(_review).where(_review.c.id == row.id).values(blocking_impact=impact)
        )
        mark_changed(s, LIVE_ENTITY, row.id)
        changed += 1
    return changed


async def refresh_review_impact_of_task(s: AsyncSession, task_id: UUID) -> int:
    """refresh_review_impact for the task's project (trashed or not); 0 when the task is
    gone."""
    project_id: UUID | None = await s.scalar(
        select(_tasks.c.project_id).where(_tasks.c.id == task_id)
    )
    return 0 if project_id is None else await refresh_review_impact(s, project_id)


async def review_impact_facts(s: AsyncSession, item_id: UUID) -> ImpactFacts | None:
    """What an open item blocks (decisions' blocking-impact inputs); None when the item is
    decided, trashed or gone, or its kind is not registered."""
    row = (await s.execute(_select().where(_review.c.id == item_id, OPEN))).mappings().first()
    spec = None if row is None else _KINDS.get(row["kind"])
    if row is None or spec is None:
        return None
    project = await _scope_project(s, row["target_type"], row["target_id"], row["project_id"])
    graph = await _graph(s, spec.impact_scope, project)
    target_task = _target_task(row["target_type"], row["target_id"])
    tasks, minutes = rules.downstream(target_task, spec.impact_scope, graph)
    names = await projects.project_names(s, [] if project is None else [project])
    return ImpactFacts(
        item_id=item_id,
        kind=spec.kind,
        project_id=row["project_id"],
        target_title=row["target_title"],
        project_name=names.get(project) if project is not None else None,
        tasks=tasks,
        minutes=minutes,
        nearest_due=rules.nearest_due(target_task, spec.impact_scope, graph),
    )


async def set_review_jev(
    item_id: UUID, *, jev_factor: float, decision_id: UUID, session: AsyncSession | None = None
) -> bool:
    """Stores Jev's blocking-impact factor and its decision on an open item (decisions
    calls it once per item); False when the item is no longer open."""
    async with _session(session) as s:
        found = await s.scalar(
            update(_review)
            .where(_review.c.id == item_id, text(OPEN_WHERE))
            .values(jev_factor=jev_factor, decision_id=decision_id)
            .returning(_review.c.id)
        )
        if found is None:
            return False
        mark_changed(s, LIVE_ENTITY, item_id)
        return True


# --- The queue ---------------------------------------------------------------------------------

# rules.review_key in SQL: the biggest weighted impact first (negated, so the keyset runs
# ascending), then the oldest, then the id.
WEIGHTED: Final = -(
    cast(func.coalesce(_review.c.blocking_impact, "0"), Float)
    * func.coalesce(_review.c.jev_factor, 1.0)
)


def _select() -> Select[Any]:
    """Items with their task target's title (a project's is filled in after)."""
    title = _tasks.c.title.label("target_title")
    joined = _review.outerjoin(
        _tasks,
        and_(
            _review.c.target_type == "task",
            _tasks.c.id == _review.c.target_id,
            _tasks.c.deleted_at.is_(None),
        ),
    )
    return select(_review, title).select_from(joined)


async def _with_project_titles(s: AsyncSession, items: list[ReviewItemOut]) -> list[ReviewItemOut]:
    wanted = {i.target_id for i in items if i.target_type == "project" and i.target_title is None}
    names = await projects.project_names(s, wanted)
    return [
        item.model_copy(update={"target_title": names[item.target_id]})
        if item.target_type == "project" and item.target_id in names
        else item
        for item in items
    ]


async def list_review_items(
    s: AsyncSession,
    *,
    now: datetime,
    kind: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> Page[ReviewItemOut]:
    """The open queue at `now` (not decided, trashed or snoozed past `now`), optionally of
    one kind, by weighted impact, then age, then id."""
    stmt = _select().where(
        OPEN,
        or_(_review.c.snoozed_until.is_(None), _review.c.snoozed_until <= now),
    )
    if kind is not None:
        stmt = stmt.where(_review.c.kind == kind)
    page = await paginate(
        s,
        stmt,
        keys=[SortKey(WEIGHTED), SortKey(_review.c.created_at)],
        id_col=_review.c.id,
        cursor=cursor,
        limit=limit,
        model=ReviewItemOut,
    )
    return Page[ReviewItemOut](
        items=await _with_project_titles(s, page.items), next_cursor=page.next_cursor
    )


async def _item_row(s: AsyncSession, item_id: UUID) -> RowMapping:
    row: RowMapping | None = (
        (await s.execute(_select().where(_review.c.id == item_id, _review.c.deleted_at.is_(None))))
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("review_items", item_id)
    return row


async def get_review_item(s: AsyncSession, item_id: UUID) -> ReviewItemOut:
    """One live item, decided or not; NotFound (404) otherwise."""
    [item] = await _with_project_titles(s, [ReviewItemOut.from_row(await _item_row(s, item_id))])
    return item


def _decision_id(payload: Mapping[str, Any]) -> UUID | None:
    """The decision the item stands for (its payload's `decision_id`), for the log's
    outcome (FR-11.5); never the blocking-impact one."""
    value = payload.get("decision_id")
    try:
        return None if value is None else UUID(str(value))
    except ValueError:
        return None


async def _decide(  # noqa: PLR0917  # decide_review_item's fields, spelled out
    s: AsyncSession,
    item_id: UUID,
    action: str,
    payload: Mapping[str, Any] | None,
    snooze_until: datetime | None,
    version: int,
    now: datetime,
) -> ReviewItemOut:
    row = await _item_row(s, item_id)
    spec = _spec(row["kind"])
    body = validate_decision(spec, action, payload, snooze_until=snooze_until, now=now)
    if row["decided_at"] is not None:
        raise ProblemError(409, "already_decided", "This item was already decided")
    values: dict[str, Any] = (
        {"snoozed_until": snooze_until}
        if action == SNOOZE
        else {"decided_at": now, "decision": action}
    )
    await update_versioned(s, _review, item_id, version, values)
    await emit(
        s,
        HumanDecidedV1(
            item_kind=spec.kind,
            item_id=item_id,
            target_type=row["target_type"],
            target_id=row["target_id"],
            decision=action,
            payload=body,
            decision_id=_decision_id(row["payload"] or {}),
        ),
        occurred_at=now,
    )
    mark_changed(s, LIVE_ENTITY, item_id)
    return await get_review_item(s, item_id)


async def decide_review_item(  # R-04's body, plus who and when
    item_id: UUID,
    *,
    action: str,
    payload: Mapping[str, Any] | None,
    snooze_until: datetime | None,
    version: int,
    actor: ActorRef,
    now: datetime | None = None,
    session: AsyncSession | None = None,
) -> ReviewItemOut:
    """Decides an open item at `version` (R-04): 422 `action_not_allowed`,
    `invalid_snooze` or `invalid_review_payload` (validate_decision), 404 for a missing
    item, 409 `already_decided`, then 409 `stale_version`. A snooze sets `snoozed_until`;
    any other action closes the item with `decision`. Emits `human.decided` (R-07)."""
    at = now if now is not None else SystemClock().now()
    if session is not None:
        return await _decide(session, item_id, action, payload, snooze_until, version, at)
    async with tenant_session(WorkspaceContext(_context().workspace_id, actor)) as s:
        return await _decide(s, item_id, action, payload, snooze_until, version, at)


async def review_badge_count(s: AsyncSession, now: datetime) -> int:
    """Open items: not decided, not trashed, and not snoozed past `now`."""
    count: int | None = await s.scalar(
        select(func.count())
        .select_from(_review)
        .where(
            text(OPEN_WHERE),
            or_(_review.c.snoozed_until.is_(None), _review.c.snoozed_until <= now),
        )
    )
    return count or 0


# --- tasks' own kinds (P1-13; P1-07 and P1-08 queue them) --------------------------------------

LABEL_KIND: Final = "low_confidence_label"
ESTIMATE_KIND: Final = "estimate_outlier"


class LowConfidenceLabelPayload(BaseModel):
    """Jev's label for a task was not confident enough to apply (P1-07): its suggestion."""

    suggested: rules.Label
    probabilities: dict[str, float] = {}
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None
    decision_id: UUID | None = None


class LabelEdit(BaseModel):
    label: rules.Label


class EstimateOutlierPayload(BaseModel):
    """A task's estimate looks implausible against history (P1-08)."""

    estimate_minutes: int | None = None
    flag: Literal["too_low", "too_high"]
    score: float | None = None
    history_median: int | None = None
    decision_id: UUID | None = None


class EstimateEdit(BaseModel):
    estimate_minutes: Annotated[int, Field(gt=0, le=MAX_ESTIMATE_MINUTES)]


LOW_CONFIDENCE_LABEL: Final = ReviewKindSpec(
    kind=LABEL_KIND,
    owner_module="tasks",
    payload_schema=LowConfidenceLabelPayload,
    actions=("accept", "edit", "reject", "snooze"),  # accept sets the suggested label
    impact_scope="task",
    action_payloads=MappingProxyType({"edit": LabelEdit}),
)
ESTIMATE_OUTLIER: Final = ReviewKindSpec(
    kind=ESTIMATE_KIND,
    owner_module="tasks",
    payload_schema=EstimateOutlierPayload,
    actions=("accept", "edit", "reject", "snooze"),  # accept keeps the estimate
    impact_scope="task",
    action_payloads=MappingProxyType({"edit": EstimateEdit}),
)
register_review_kind(LOW_CONFIDENCE_LABEL)
register_review_kind(ESTIMATE_OUTLIER)


# --- Flags (P2-13) ---------------------------------------------------------------------------

RESULT_KIND: Final = "result"  # P2-04 queues these: an agent's finished work, with its links
CHECKS_RED: Final = "checks_red"


async def flag_pull_request_results(s: AsyncSession, key: str, *, red: bool) -> int:
    """Sets (`red`) or clears the `checks_red` flag on the open `result` review items whose
    links name the pull request `key` (`owner/repo#number`, `github.pull_request_key`). A
    decided or trashed item is left alone, and an item already in the wanted state is not
    touched, so a repeated delivery changes nothing. Returns the items changed."""
    repo, _, number = key.rpartition("#")
    needle = f"/{repo}/pull/{number}".lower()
    rows = (
        await s.execute(
            select(_review.c.id, _review.c.payload, _review.c.flags).where(
                _review.c.kind == RESULT_KIND,
                text(OPEN_WHERE),
                func.strpos(func.lower(_review.c.payload["links"].astext), needle) > 0,
            )
        )
    ).all()
    changed = 0
    for item_id, payload, flags in rows:
        links = payload.get("links") if isinstance(payload, dict) else None
        names = [
            github.pull_request_key(str(link.get("url", "")))
            for link in (links if isinstance(links, list) else [])
            if isinstance(link, dict)
        ]
        if key not in names or (CHECKS_RED in flags) == red:
            continue
        wanted = [*flags, CHECKS_RED] if red else [f for f in flags if f != CHECKS_RED]
        await s.execute(_review.update().where(_review.c.id == item_id).values(flags=wanted))
        mark_changed(s, LIVE_ENTITY, item_id)
        changed += 1
    return changed
