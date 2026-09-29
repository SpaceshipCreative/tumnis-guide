"""tasks public functions and DTOs; the only file other modules may import (P0-18).

Tasks carry every FR-3.1 field and move only through the state machine in `rules.py`
(`change_status`, R-10, and `move_task`, which changes the status when the target column
holds another). Every write runs in the caller's transaction (`s`), locks or version-checks
its row (a stale version is 409 `stale_version` with the task as it is), emits its event
there (`task.created`, `task.updated`, `task.status_changed`) and marks the task changed for
live clients. A write aimed at a task looks the task up before any body rule, so a task the
caller cannot see is 404 whatever the body says (A0.3, #28).

Actors map to the state machine's kinds: a user session is HUMAN, an API key, task token or
runner device is AGENT (a script holding a key is an agent), `system` is SYSTEM.

Board columns belong to a project: six defaults (one per status) made on `project.created`
by the `tasks.create_default_columns` subscriber, and again by any read or write that finds
none (the subscriber runs in the worker, maybe after the first request). Layout is computed
on read (`rules.layout_board` against `projects.api.effective_subtask_threshold`), never
stored, so a threshold change loses nothing.

Also here: the ReviewItem interface (R-03, `review.py`), the `ProjectStatsSource` projects'
health reads (registered at import) and the task seed writer.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import RowMapping, Table, and_, case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import rank, tenancy
from tumnis.core.clock import SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.limits import MAX_ESTIMATE_MINUTES
from tumnis.core.live import mark_changed
from tumnis.core.outbox import emit
from tumnis.core.pagination import Page, SortKey, paginate
from tumnis.core.routing import register_project_lookup
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.core.versioning import NotFound, StaleVersion, Version, update_versioned
from tumnis.modules.integrations import api as integrations
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import rules
from tumnis.modules.tasks.models import BoardColumn, Task, TaskComment, TaskContextItem
from tumnis.modules.tasks.payloads import (
    DOC_BODY_MAX_BYTES,
    TaskCreatedV1,
    TaskDoc,
    TaskStatusChangedV1,
    TaskUpdatedV1,
)
from tumnis.modules.tasks.review import (
    DuplicateReviewKind,
    ReviewKindSpec,
    TargetRef,
    UnknownReviewKind,
    add_review_item,
    register_review_kind,
    review_badge_count,
    review_kinds,
)
from tumnis.modules.tasks.rules import ActorKind, Label, Status
from tumnis.seed import TaskSeed, register_seed_writer

__all__ = [
    "ActorKind",
    "DuplicateReviewKind",
    "Label",
    "ReviewKindSpec",
    "Status",
    "TargetRef",
    "UnknownReviewKind",
    "add_review_item",
    "register_review_kind",
    "review_badge_count",
    "review_kinds",
]

_tasks: Table = Task.__table__  # type: ignore[assignment]
_columns: Table = BoardColumn.__table__  # type: ignore[assignment]
_comments: Table = TaskComment.__table__  # type: ignore[assignment]
_links: Table = TaskContextItem.__table__  # type: ignore[assignment]

LIVE_ENTITY: Final = "task"
PROJECT_ENTITY: Final = "project"  # column edits refresh the project's views
Priority = Literal["low", "normal", "high", "urgent"]
LabelSource = Literal["user", "jev", "agent", "fallback"]
TaskOrder = Literal["created", "today"]
Title = Annotated[str, StringConstraints(min_length=1, max_length=500, strip_whitespace=True)]
Estimate = Annotated[int, Field(gt=0, le=MAX_ESTIMATE_MINUTES)]
LongText = Annotated[str, StringConstraints(max_length=8_000)]
ColumnName = Annotated[str, StringConstraints(min_length=1, max_length=60, strip_whitespace=True)]
BoardRank = Annotated[str, StringConstraints(min_length=1, max_length=rank.MAX_KEY_LEN)]

DEFAULT_COLUMNS: Final = (
    ("Backlog", Status.BACKLOG),
    ("Today", Status.TODAY),
    ("In progress", Status.IN_PROGRESS),
    ("Waiting on human", Status.WAITING_ON_HUMAN),
    ("In review", Status.IN_REVIEW),
    ("Done", Status.DONE),
)
OPEN_STATUSES: Final = frozenset(Status) - {Status.DONE}
SEED_PRIORITIES: Final[tuple[Priority, ...]] = ("low", "normal", "high", "urgent")
_LABEL_SOURCE: Final[dict[ActorKind, LabelSource]] = {
    ActorKind.HUMAN: "user",
    ActorKind.AGENT: "agent",
    ActorKind.SYSTEM: "fallback",
}
_SOURCE: Final[dict[ActorKind, str]] = {
    ActorKind.HUMAN: "user",
    ActorKind.AGENT: "agent",
    ActorKind.SYSTEM: "system",
}


# --- DTOs ------------------------------------------------------------------------------------


class TaskCreate(BaseModel):
    schema_version: Literal[1] = 1
    project_id: UUID
    parent_id: UUID | None = None
    title: Title
    label: Label | None = None  # None = pending (R-08) until the user or Jev (P1-07) sets it
    priority: Priority = "normal"
    due_on: date | None = None
    estimate_minutes: Estimate | None = None  # minutes of human time (core/limits.py, R-11)
    first_action: LongText | None = None
    acceptance_criteria: LongText | None = None
    status: Literal["backlog", "today"] = "backlog"


class TaskOut(BaseModel):
    schema_version: Literal[1] = 1
    id: UUID
    project_id: UUID
    parent_id: UUID | None
    title: str
    label: Label | None
    label_source: LabelSource | None
    status: Status
    priority: Priority
    due_on: date | None
    estimate_minutes: int | None
    first_action: str | None
    acceptance_criteria: str | None
    assigned_agent_id: UUID | None
    column_id: UUID | None
    board_rank: str
    rollover_count: int
    started_at: datetime | None
    completed_at: datetime | None
    actual_minutes: int | None
    tainted: bool
    source: str
    version: int
    created_at: datetime
    updated_at: datetime


class TaskPage(Page[TaskOut]):
    """A page of tasks and how many match the filter across every page (P0-23): the Today
    panel shows five and says "+N more"."""

    total: int


class TaskPatch(BaseModel):
    """Fields to change (absent: unchanged; null clears a nullable one) and the version
    read. Status changes go through `POST /status` or `/move`."""

    title: Title | None = None
    label: Label | None = None
    priority: Priority | None = None
    due_on: date | None = None
    estimate_minutes: Estimate | None = None
    first_action: LongText | None = None
    acceptance_criteria: LongText | None = None
    version: Version


class CommentOut(BaseModel):
    id: UUID
    task_id: UUID
    body_md: str
    created_by: str
    created_at: datetime
    version: int


class TaskContextItemOut(BaseModel):
    """A task's link to outside content: the ContextItem and what it points at."""

    id: UUID
    task_id: UUID
    context_item_id: UUID
    target_type: str
    target_id: UUID | None
    target_url: str | None
    tainted: bool


class ColumnIn(BaseModel):
    id: UUID | None = None  # an existing column of the project; None adds one
    name: ColumnName
    status: Status


class ColumnOut(BaseModel):
    id: UUID
    name: str
    status: Status
    sort_key: str
    version: int


class ColumnsOut(BaseModel):
    project_id: UUID
    items: list[ColumnOut]


class CardOut(BaseModel):
    task: TaskOut
    checklist: list[TaskOut]  # nested subtasks (FR-3.4), in board order


class BoardColumnOut(BaseModel):
    id: UUID
    name: str
    status: Status
    cards: list[CardOut]


class BoardOut(BaseModel):
    project_id: UUID
    threshold_min: int
    columns: list[BoardColumnOut]


# --- Helpers ---------------------------------------------------------------------------------


def actor_kind(actor: ActorRef) -> ActorKind:
    """user session -> HUMAN; `system` -> SYSTEM; an API key, task token or device ->
    AGENT."""
    if actor == SYSTEM_ACTOR:
        return ActorKind.SYSTEM
    return ActorKind.HUMAN if str(actor).startswith("user:") else ActorKind.AGENT


def _now(now: datetime | None) -> datetime:
    return now if now is not None else SystemClock().now()


def _context() -> WorkspaceContext:
    ctx = tenancy.current()
    if ctx is None:
        raise RuntimeError("tasks api called outside a workspace context")
    return ctx


def _live(table: Table) -> Any:
    return table.c.deleted_at.is_(None)


def _out(row: Mapping[Any, Any]) -> TaskOut:
    return TaskOut.model_validate(dict(row))


def _label(value: str | None) -> Label | None:
    return None if value is None else Label(value)


async def _row(s: AsyncSession, task_id: UUID, *, lock: bool = False) -> RowMapping:
    stmt = select(_tasks).where(_tasks.c.id == task_id, _live(_tasks))
    if lock:
        stmt = stmt.with_for_update()
    found = (await s.execute(stmt)).mappings().first()
    if found is None:
        raise NotFound("tasks", task_id)
    return found


def _stale(row: Mapping[Any, Any]) -> StaleVersion:
    return StaleVersion(current=_out(row).model_dump(mode="json"))


async def _versioned(
    s: AsyncSession, task_id: UUID, version: int, values: Mapping[Any, Any]
) -> RowMapping:
    try:
        return await update_versioned(s, _tasks, task_id, version, values)
    except StaleVersion as exc:
        raise _stale(exc.current) from None


async def _doc(s: AsyncSession, row: Mapping[Any, Any], *, at: datetime | None = None) -> TaskDoc:
    """The search document: title, then first action, acceptance criteria and comments
    capped at 8 KB (plan default); its project, and `at`, the change's time (P0-20)."""
    comments: Sequence[str] = (
        await s.scalars(
            select(_comments.c.body_md)
            .where(_comments.c.task_id == row["id"], _live(_comments))
            .order_by(_comments.c.id)
        )
    ).all()
    parts = [row["first_action"], row["acceptance_criteria"], *comments]
    body = "\n\n".join(part for part in parts if part)
    capped = body.encode()[:DOC_BODY_MAX_BYTES].decode(errors="ignore")
    return TaskDoc(
        title=row["title"],
        body=capped,
        deleted=row["deleted_at"] is not None,
        project_id=row["project_id"],
        updated_at=at,
    )


async def project_of(ctx: WorkspaceContext, task_id: UUID) -> UUID | None:
    """The task's project in `ctx`'s workspace (the `lookup:tasks` routes' project, R-28)."""
    async with tenant_session(ctx) as s:
        found: UUID | None = await s.scalar(
            select(_tasks.c.project_id).where(_tasks.c.id == task_id, _live(_tasks))
        )
    return found


register_project_lookup("tasks", project_of)


# --- Reading ---------------------------------------------------------------------------------


async def get_task(s: AsyncSession, task_id: UUID) -> TaskOut:
    return _out(await _row(s, task_id))


async def list_tasks(
    s: AsyncSession,
    *,
    project_id: UUID | None = None,
    status: Status | None = None,
    order: TaskOrder = "created",
    cursor: str | None = None,
    limit: int = 50,
    project_ids: frozenset[UUID] | None = None,
) -> TaskPage:
    """Live tasks, optionally of one project or one status, with the filter's `total`.
    `order="created"` is creation order; `order="today"` is `rules.today_order` (priority,
    then due date with undated last, then oldest, then id) as keyset keys, so the pages
    walk the same order. `project_ids` limits them (a project-limited key, R-28)."""
    stmt = select(_tasks).where(_live(_tasks))
    if project_id is not None:
        stmt = stmt.where(_tasks.c.project_id == project_id)
    if status is not None:
        stmt = stmt.where(_tasks.c.status == status)
    if project_ids is not None:
        stmt = stmt.where(_tasks.c.project_id.in_(project_ids))
    total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
    keys = _TODAY_KEYS if order == "today" else []
    page = await paginate(
        s, stmt, keys=keys, id_col=_tasks.c.id, cursor=cursor, limit=limit, model=TaskOut
    )
    return TaskPage(items=page.items, next_cursor=page.next_cursor, total=total or 0)


# `rules.today_order` in SQL: the same keys, ranks from `rules.PRIORITY_RANK`.
_TODAY_KEYS: Final = (
    SortKey(
        case(
            *((_tasks.c.priority == name, rank) for name, rank in rules.PRIORITY_RANK.items()),
            else_=len(rules.PRIORITY_RANK),
        )
    ),
    SortKey(_tasks.c.due_on.is_(None)),
    SortKey(_tasks.c.due_on, nulls_last_sentinel=date.max),
    SortKey(_tasks.c.created_at),
)


# --- Columns ---------------------------------------------------------------------------------


async def _require_project(s: AsyncSession, project_id: UUID) -> None:
    if not await projects.project_exists(s, project_id):
        raise NotFound("projects", project_id)


async def _column_rows(s: AsyncSession, project_id: UUID) -> list[RowMapping]:
    rows = await s.execute(
        select(_columns)
        .where(_columns.c.project_id == project_id, _live(_columns))
        .order_by(_columns.c.sort_key, _columns.c.id)
    )
    return list(rows.mappings())


async def ensure_default_columns(s: AsyncSession, project_id: UUID) -> None:
    """The six default columns (FR-3.2), written once: nothing while the project has any
    live column or does not exist. Serialized per project by an advisory lock, so the
    subscriber and a first request never both write them."""
    lock_key = func.hashtextextended(f"tasks.board_columns:{project_id}", 0)
    await s.execute(select(func.pg_advisory_xact_lock(lock_key)))
    if not await projects.project_exists(s, project_id):
        return
    if await _column_rows(s, project_id):
        return
    keys = rank.n_keys(len(DEFAULT_COLUMNS))
    await s.execute(
        pg_insert(_columns),
        [
            {"project_id": project_id, "name": name, "status_map": status, "sort_key": key}
            for (name, status), key in zip(DEFAULT_COLUMNS, keys, strict=True)
        ],
    )


def _column_out(row: Mapping[Any, Any]) -> ColumnOut:
    return ColumnOut(
        id=row["id"],
        name=row["name"],
        status=row["status_map"],
        sort_key=row["sort_key"],
        version=row["version"],
    )


async def list_columns(s: AsyncSession, project_id: UUID) -> ColumnsOut:
    """The project's columns in board order (the defaults when it has none yet)."""
    await _require_project(s, project_id)
    await ensure_default_columns(s, project_id)
    rows = await _column_rows(s, project_id)
    return ColumnsOut(project_id=project_id, items=[_column_out(row) for row in rows])


async def put_columns(
    s: AsyncSession,
    actor: ActorRef,
    project_id: UUID,
    columns: Sequence[ColumnIn],
    *,
    now: datetime | None = None,
) -> ColumnsOut:
    """Replaces the project's columns with `columns`, in that order: listed ids keep their
    row (renamed, remapped, moved), entries without an id are added, columns left out are
    removed (their tasks show in the first column for their status). 422
    `status_without_column` when a status would have no column, `unknown_column` for an id
    that is not one of the project's columns (or is listed twice)."""
    await _require_project(s, project_id)  # 404 before any body rule (A0.3)
    await ensure_default_columns(s, project_id)
    missing = set(Status) - {column.status for column in columns}
    if missing:
        names = ", ".join(sorted(missing))
        raise ProblemError(422, "status_without_column", f"No column holds: {names}")
    current = {row["id"]: row for row in await _column_rows(s, project_id)}
    given = [column.id for column in columns if column.id is not None]
    if len(set(given)) != len(given) or not set(given) <= set(current):
        raise ProblemError(422, "unknown_column", "Name each of the project's columns once")
    for column, key in zip(columns, rank.n_keys(len(columns)), strict=True):
        values = {"name": column.name, "status_map": column.status, "sort_key": key}
        if column.id is None:
            await s.execute(pg_insert(_columns).values(project_id=project_id, **values))
            continue
        row = current[column.id]
        if any(row[field] != value for field, value in values.items()):
            await s.execute(update(_columns).where(_columns.c.id == column.id).values(**values))
    dropped = set(current) - set(given)
    if dropped:
        await s.execute(
            update(_columns).where(_columns.c.id.in_(dropped)).values(deleted_at=_now(now))
        )
    mark_changed(s, PROJECT_ENTITY, project_id)
    rows = await _column_rows(s, project_id)
    return ColumnsOut(project_id=project_id, items=[_column_out(row) for row in rows])


async def _slot(
    s: AsyncSession, project_id: UUID, status: Status, *, column_id: UUID | None = None
) -> tuple[UUID | None, str]:
    """A column holding `status` (the given one if it does, else the first) and a rank
    after the last task in it."""
    await ensure_default_columns(s, project_id)
    stmt = select(_columns.c.id).where(
        _columns.c.project_id == project_id,
        _columns.c.status_map == status,
        _live(_columns),
    )
    if column_id is not None:
        stmt = stmt.where(_columns.c.id == column_id)
    target: UUID | None = await s.scalar(stmt.order_by(_columns.c.sort_key, _columns.c.id).limit(1))
    last: str | None = await s.scalar(
        select(func.max(_tasks.c.board_rank)).where(
            _tasks.c.project_id == project_id,
            _tasks.c.column_id.is_not_distinct_from(target),
            _live(_tasks),
        )
    )
    try:
        return target, rank.between(last, None)
    except rank.RankError:  # a key written outside the api: start the column afresh
        return target, rank.between(None, None)


def _rank_or_422(key: str) -> str:
    try:
        rank.validate(key)
    except rank.RankError as exc:
        raise ProblemError(422, exc.code, str(exc)) from None
    return key


# --- Writing ---------------------------------------------------------------------------------


def _estimate(label: Label | None, estimate: int | None, kind: ActorKind) -> int | None:
    try:
        return rules.normalize_estimate(label, estimate, kind)
    except rules.EstimateRequired as exc:
        raise ProblemError(422, exc.code, str(exc)) from None


async def _check_parent(s: AsyncSession, parent_id: UUID, project_id: UUID) -> None:
    """A subtask's parent is a live root task of the same project (depth one, plan
    default): 404 when there is no such task, 422 `parent_project_mismatch` or
    `parent_not_root` otherwise."""
    parent = await _row(s, parent_id)
    if parent["project_id"] != project_id:
        raise ProblemError(
            422, "parent_project_mismatch", "A subtask belongs to its parent's project"
        )
    if parent["parent_id"] is not None:
        raise ProblemError(422, "parent_not_root", "A subtask cannot have subtasks")


async def _insert(
    s: AsyncSession,
    actor: ActorRef,
    values: dict[str, Any],
    *,
    now: datetime | None,
    source: str,
) -> TaskOut:
    column_id, board_rank = await _slot(s, values["project_id"], Status(values["status"]))
    created = (
        (
            await s.execute(
                pg_insert(_tasks)
                .values(
                    **values,
                    column_id=column_id,
                    board_rank=board_rank,
                    source=source,
                    created_by=actor,
                )
                .returning(*_tasks.c)
            )
        )
        .mappings()
        .one()
    )
    at = _now(now)
    await emit(
        s,
        TaskCreatedV1(
            task_id=created["id"],
            project_id=created["project_id"],
            label=_label(created["label"]),
            source=created["source"],
            tainted=created["tainted"],
            doc=await _doc(s, created, at=at),
        ),
        occurred_at=at,
    )
    mark_changed(s, LIVE_ENTITY, created["id"])
    return _out(created)


async def create_task(
    s: AsyncSession,
    actor: ActorRef,
    data: TaskCreate,
    *,
    now: datetime | None = None,
    source: str | None = None,
    label_source: LabelSource | None = None,
) -> TaskOut:
    """A task in Backlog or Today, last in its column; emits `task.created`. 404 for a
    project (or parent) the caller cannot see; 422 `estimate_required` (an agent's Human or
    Hybrid task without an estimate), `parent_project_mismatch`, `parent_not_root`. An AI
    task's estimate is dropped. `label_source` defaults from the actor (user, agent,
    fallback); `source` says where the task came from (default: user, agent or system)."""
    await _require_project(s, data.project_id)
    if data.parent_id is not None:
        await _check_parent(s, data.parent_id, data.project_id)
    kind = actor_kind(actor)
    values = data.model_dump(exclude={"schema_version"})
    values["estimate_minutes"] = _estimate(data.label, data.estimate_minutes, kind)
    values["label_source"] = None if data.label is None else (label_source or _LABEL_SOURCE[kind])
    return await _insert(s, actor, values, now=now, source=source or _SOURCE[kind])


async def _changed(
    s: AsyncSession, row: Mapping[Any, Any], fields: Sequence[str], now: datetime | None
) -> None:
    at = _now(now)
    await emit(
        s,
        TaskUpdatedV1(
            task_id=row["id"], changed_fields=sorted(fields), doc=await _doc(s, row, at=at)
        ),
        occurred_at=at,
    )
    mark_changed(s, LIVE_ENTITY, row["id"])


async def update_task(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    patch: TaskPatch,
    version: int,
    *,
    now: datetime | None = None,
    label_source: LabelSource | None = None,
) -> TaskOut:
    """Changes the fields the patch sets at `version`; emits `task.updated` with the names
    of the fields whose value changed (R-06). Setting a label records who set it
    (`label_source`); the estimate rule applies whenever the label or estimate changes."""
    row = await _row(s, task_id)  # 404 before any body rule (A0.3, #28)
    values = patch.model_dump(exclude_unset=True, exclude={"version"})
    for required in ("title", "priority"):
        if required in values and values[required] is None:
            raise ProblemError(422, "validation_error", f"{required} cannot be null")
    kind = actor_kind(actor)
    if "label" in values or "estimate_minutes" in values:
        label = values["label"] if "label" in values else _label(row["label"])
        estimate = values.get("estimate_minutes", row["estimate_minutes"])
        values["estimate_minutes"] = _estimate(label, estimate, kind)
    if "label" in values:
        values["label_source"] = (
            None if values["label"] is None else (label_source or _LABEL_SOURCE[kind])
        )
    changed = [field for field, value in values.items() if row[field] != value]
    updated = await _versioned(s, task_id, version, values or {"updated_at": func.now()})
    if changed:
        await _changed(s, updated, changed, now)
    return _out(updated)


async def _transition(  # one path for /status and /move
    s: AsyncSession,
    actor: ActorRef,
    row: RowMapping,
    to: Status,
    version: int,
    *,
    now: datetime | None,
    column_id: UUID | None = None,
    board_rank: str | None = None,
) -> TaskOut:
    """Checks the version (409 `stale_version` first, so the client gets the freshest
    row), then the edge (409 `transition_not_allowed` with `current`), applies the side
    effects, places the task in a column for `to` and emits `task.status_changed`."""
    if row["version"] != version:
        raise _stale(row)
    at = _now(now)
    frm = Status(row["status"])
    state = rules.TaskState(
        frm,
        _label(row["label"]),
        row["rollover_count"],
        row["started_at"],
        row["completed_at"],
        row["actual_minutes"],
    )
    try:
        after = rules.apply_transition(state, to, actor_kind(actor), at)
    except rules.TransitionNotAllowed as exc:
        raise ProblemError(
            409, exc.code, str(exc), current=_out(row).model_dump(mode="json")
        ) from None
    target, key = await _slot(s, row["project_id"], to, column_id=column_id)
    updated = await _versioned(
        s,
        row["id"],
        version,
        {
            "status": after.status,
            "rollover_count": after.rollover_count,
            "started_at": after.started_at,
            "completed_at": after.completed_at,
            "actual_minutes": after.actual_minutes,
            "column_id": target,
            "board_rank": board_rank or key,
        },
    )
    await emit(
        s,
        TaskStatusChangedV1(task_id=row["id"], from_=frm, to=to, actor=str(actor)),
        occurred_at=at,
    )
    mark_changed(s, LIVE_ENTITY, row["id"])
    return _out(updated)


async def change_status(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    to: Status,
    version: int,
    *,
    now: datetime | None = None,
) -> TaskOut:
    """Moves the task to `to` through the state machine (R-10): locks the row, checks the
    version, then the edge, applies the side effects, bumps the version and writes the
    outbox row, in the caller's transaction."""
    row = await _row(s, task_id, lock=True)
    return await _transition(s, actor, row, Status(to), version, now=now)


async def move_task(  # noqa: PLR0917  # R-20's body, plus who and when
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    column_id: UUID,
    board_rank: str,
    version: int,
    *,
    now: datetime | None = None,
) -> TaskOut:
    """One board drag (R-20): puts the task in `column_id` at `board_rank`. A column of
    another status changes the status through the state machine (`task.status_changed`);
    within a status it is a plain reorder (`task.updated`). 404 for a column that is not
    one of the task's project; 422 `invalid_rank`."""
    row = await _row(s, task_id, lock=True)  # 404 before any body rule (A0.3, #28)
    column = (
        (
            await s.execute(
                select(_columns).where(
                    _columns.c.id == column_id,
                    _columns.c.project_id == row["project_id"],
                    _live(_columns),
                )
            )
        )
        .mappings()
        .first()
    )
    if column is None:
        raise NotFound("board_columns", column_id)
    key = _rank_or_422(board_rank)
    to = Status(column["status_map"])
    if to != row["status"]:
        return await _transition(
            s, actor, row, to, version, now=now, column_id=column_id, board_rank=key
        )
    values = {"column_id": column_id, "board_rank": key}
    changed = [field for field, value in values.items() if row[field] != value]
    updated = await _versioned(s, task_id, version, values)
    if changed:
        await _changed(s, updated, changed, now)
    return _out(updated)


async def add_comment(
    s: AsyncSession, actor: ActorRef, task_id: UUID, body_md: str, *, now: datetime | None = None
) -> CommentOut:
    """A markdown comment on the task; `task.updated` carries `comments` and the new doc."""
    row = await _row(s, task_id)
    created = (
        (
            await s.execute(
                pg_insert(_comments)
                .values(task_id=task_id, body_md=body_md, created_by=actor)
                .returning(*_comments.c)
            )
        )
        .mappings()
        .one()
    )
    await _changed(s, row, ["comments"], now)
    return CommentOut.model_validate(dict(created))


async def link_context_item(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    context_item_id: UUID,
    *,
    now: datetime | None = None,
) -> TaskContextItemOut:
    """Links the task to outside content through a ContextItem (FR-14.2), the only way a
    task reaches a message, note, event, artifact, file or URL. Linking again keeps one
    link. 404 for a task or context item the caller cannot see."""
    await _row(s, task_id)
    item = await integrations.get_context_item_ref(_context(), context_item_id, session=s)
    if item is None:
        raise NotFound("context_items", context_item_id)
    await s.execute(
        pg_insert(_links)
        .values(task_id=task_id, context_item_id=context_item_id, created_by=actor)
        .on_conflict_do_update(
            index_elements=[_links.c.workspace_id, _links.c.task_id, _links.c.context_item_id],
            set_={"deleted_at": None},
            where=_links.c.deleted_at.is_not(None),
        )
    )
    link_id: UUID | None = await s.scalar(
        select(_links.c.id).where(
            _links.c.task_id == task_id, _links.c.context_item_id == context_item_id
        )
    )
    assert link_id is not None  # noqa: S101  # the upsert left one live link
    mark_changed(s, LIVE_ENTITY, task_id)
    return TaskContextItemOut(
        id=link_id,
        task_id=task_id,
        context_item_id=item.id,
        target_type=item.target_type,
        target_id=item.target_id,
        target_url=item.target_url,
        tainted=item.tainted,
    )


# --- Board -----------------------------------------------------------------------------------


async def board(s: AsyncSession, project_id: UUID) -> BoardOut:
    """The project's kanban: its columns with their cards, each card with its checklist
    of nested subtasks, laid out against the effective card threshold (FR-3.4, FR-3.8)."""
    threshold = await projects.effective_subtask_threshold(s, project_id)  # 404 first
    await ensure_default_columns(s, project_id)
    columns = await _column_rows(s, project_id)
    rows = (
        await s.execute(select(_tasks).where(_tasks.c.project_id == project_id, _live(_tasks)))
    ).mappings()
    tasks = {row["id"]: _out(row) for row in rows}
    layout = rules.layout_board(
        [
            rules.BoardTask(
                t.id, t.parent_id, t.status, t.column_id, t.board_rank, t.label, t.estimate_minutes
            )
            for t in tasks.values()
        ],
        [rules.ColumnDef(row["id"], Status(row["status_map"])) for row in columns],
        threshold,
    )
    by_column = {placed.column_id: placed for placed in layout.columns}
    return BoardOut(
        project_id=project_id,
        threshold_min=threshold,
        columns=[
            BoardColumnOut(
                id=row["id"],
                name=row["name"],
                status=row["status_map"],
                cards=[
                    CardOut(
                        task=tasks[card.task_id],
                        checklist=[tasks[item] for item in card.checklist],
                    )
                    for card in by_column[row["id"]].cards
                ],
            )
            for row in columns
        ],
    )


# --- Project statistics (projects' health, FR-1.1) --------------------------------------------


class TasksProjectStats:
    """projects' `ProjectStatsSource`: per project, open tasks waiting on the human, open
    tasks due before the workspace's today, open tasks and the next open due date, in one
    query for every id asked."""

    async def stats(
        self, s: AsyncSession, project_ids: Sequence[UUID], today: date
    ) -> Mapping[UUID, projects.ProjectStats]:
        if not project_ids:
            return {}
        is_open = _tasks.c.status.in_(sorted(OPEN_STATUSES))
        rows = await s.execute(
            select(
                _tasks.c.project_id,
                func.count().filter(_tasks.c.status == Status.WAITING_ON_HUMAN).label("waiting"),
                func.count().filter(and_(is_open, _tasks.c.due_on < today)).label("overdue"),
                func.count().filter(is_open).label("open"),
                func.min(_tasks.c.due_on)
                .filter(and_(is_open, _tasks.c.due_on >= today))
                .label("next_due"),
            )
            .where(_tasks.c.project_id.in_(project_ids), _live(_tasks))
            .group_by(_tasks.c.project_id)
        )
        return {
            row.project_id: projects.ProjectStats.model_validate(
                {
                    "health_facts": {"waiting_on_human": row.waiting, "overdue": row.overdue},
                    "open_count": row.open,
                    "next_open_due": row.next_due,
                }
            )
            for row in rows
        }


projects.register_stats_source(TasksProjectStats())


# --- Seed writer (P0-02's seed sets) ----------------------------------------------------------


async def seed_task(
    workspace_id: UUID, project_id: UUID, parent_id: UUID | None, rec: TaskSeed
) -> UUID:
    """A seed task as the system actor, in the status the set gives (seeds skip the state
    machine: they describe a moment, not a history). Priorities 0 to 3 are low to urgent;
    an AI task's estimate is dropped; a label comes from the user."""
    values = {
        "project_id": project_id,
        "parent_id": parent_id,
        "title": rec.title,
        "label": rec.label,
        "label_source": None if rec.label is None else "user",
        "status": rec.status,
        "priority": SEED_PRIORITIES[max(0, min(rec.priority, len(SEED_PRIORITIES) - 1))],
        "due_on": rec.due_on,
        "estimate_minutes": rules.normalize_estimate(
            _label(rec.label), rec.estimate_minutes or None, ActorKind.SYSTEM
        ),
        "first_action": rec.first_action,
        "completed_at": rec.completed_at,
    }
    async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
        out = await _insert(s, SYSTEM_ACTOR, values, now=rec.completed_at, source="seed")
    return out.id


register_seed_writer("task", seed_task)
