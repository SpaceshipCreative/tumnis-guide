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

Undo (P0-24, R-09): every task write (create, update, status, move, trash) records the
undoable fields it changed (`rules.UNDO_FIELDS`) in `task_changes`, in the same
transaction, and answers the change's id as `TaskOut.change_id`. `undo_task` puts a
change's `before` back for a person, once, while the task is still at the version the
change left; the undo is a change of its own.

Also here: the ReviewItem interface (R-03, `review.py`), the `ProjectStatsSource` projects'
health reads (registered at import) and the task seed writer.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import (
    RowMapping,
    Select,
    Table,
    and_,
    case,
    delete,
    func,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import rank, tenancy
from tumnis.core.clock import SystemClock, local_to_utc
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.limits import MAX_ESTIMATE_MINUTES
from tumnis.core.live import mark_changed
from tumnis.core.outbox import emit
from tumnis.core.pagination import Page, SortKey, paginate
from tumnis.core.routing import register_project_lookup
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.core.versioning import NotFound, StaleVersion, Version, update_versioned
from tumnis.modules.auth import api as auth
from tumnis.modules.integrations import api as integrations
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import rules
from tumnis.modules.tasks import rules_recurrence as rr
from tumnis.modules.tasks.models import (
    BoardColumn,
    DayClose,
    RecurrenceRule,
    Task,
    TaskChange,
    TaskComment,
    TaskContextItem,
)
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
from tumnis.modules.tasks.rules_recurrence import Preset
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
_changes: Table = TaskChange.__table__  # type: ignore[assignment]

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
    # Every answer carries every field (`change_id` is null on reads), so the generated
    # client types them as present (P0-24).
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

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
    # The change this write recorded (P0-24, R-09): pass it to `POST /tasks/{id}/undo`.
    # Null on reads and on a write that changed nothing undoable.
    change_id: UUID | None = None


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


async def _row(
    s: AsyncSession, task_id: UUID, *, lock: bool = False, trashed: bool = False
) -> RowMapping:
    """The task (404 when missing; a trashed one only with `trashed=True`)."""
    stmt = select(_tasks).where(_tasks.c.id == task_id)
    if not trashed:
        stmt = stmt.where(_live(_tasks))
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


async def project_tasks(s: AsyncSession, project_id: UUID) -> list[TaskOut]:
    """Every live task of one project, Done ones included, in creation order: one
    statement, for views that project the whole project (the Calendar week, P1-12)."""
    rows = await s.execute(
        select(_tasks)
        .where(_live(_tasks), _tasks.c.project_id == project_id)
        .order_by(_tasks.c.created_at, _tasks.c.id)
    )
    return [_out(row._mapping) for row in rows]


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


async def _lock_project_board(s: AsyncSession, project_id: UUID) -> None:
    """The project's board lock, held to the end of the transaction: every write that
    places a task in a column takes it (through `_slot`), so it also orders the successor
    writes of a recurrence rule (completion and the tick)."""
    lock_key = func.hashtextextended(f"tasks.board_columns:{project_id}", 0)
    await s.execute(select(func.pg_advisory_xact_lock(lock_key)))


async def ensure_default_columns(s: AsyncSession, project_id: UUID) -> None:
    """The six default columns (FR-3.2), written once: nothing while the project has any
    live column or does not exist. Serialized per project by an advisory lock, so the
    subscriber and a first request never both write them."""
    await _lock_project_board(s, project_id)
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


# --- Change log (P0-24, R-09) ------------------------------------------------------------------


async def record_change(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> UUID:
    """One `task_changes` row for a task write, in the caller's transaction: the undoable
    fields it changed (`rules.UNDO_FIELDS`, JSON values) before and after, and the task's
    version the write left (read from the row, which the caller has already written).
    Answers the change id the write returns as `TaskOut.change_id`."""
    change_id = uuid7()
    await s.execute(
        pg_insert(_changes).values(
            task_id=task_id,
            change_id=change_id,
            actor=str(actor),
            before=dict(before),
            after=dict(after),
            task_version=select(_tasks.c.version).where(_tasks.c.id == task_id).scalar_subquery(),
            created_by=actor,
        )
    )
    return change_id


async def _record(
    s: AsyncSession, actor: ActorRef, before_row: Mapping[Any, Any], after_row: Mapping[Any, Any]
) -> UUID | None:
    """Records what a write changed between two versions of the row; None (no row) when
    nothing undoable changed."""
    before, after = rules.change_between(before_row, after_row)
    if not before:
        return None
    return await record_change(s, actor, after_row["id"], before, after)


def _with_change(row: Mapping[Any, Any], change_id: UUID | None) -> TaskOut:
    return _out(row).model_copy(update={"change_id": change_id})


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
    out = await _announce_created(s, created, now)
    change_id = await record_change(
        s, actor, created["id"], {"deleted": True}, {"deleted": False}
    )  # undoing a create puts the task in the trash
    return out.model_copy(update={"change_id": change_id})


async def _announce_created(s: AsyncSession, created: RowMapping, now: datetime | None) -> TaskOut:
    """`task.created` and the live mark for a row just inserted."""
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
    return _with_change(updated, await _record(s, actor, row, updated))


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
    change_id = await _record(s, actor, row, updated)
    if to is Status.DONE and updated["recurrence_rule_id"] is not None:
        await _successor_on_done(s, updated, at)  # P0-19: the next instance, same transaction
    return _with_change(updated, change_id)


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
    return _with_change(updated, await _record(s, actor, row, updated))


async def trash_task(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    version: int,
    *,
    now: datetime | None = None,
) -> TaskOut:
    """Moves the task to the trash (`deleted_at`; the housekeeping purge removes it after
    30 days, P0-19); agents never hard-delete (UX 9). Emits `task.updated` with `deleted`;
    undoable like any write."""
    row = await _row(s, task_id, lock=True)
    if row["version"] != version:
        raise _stale(row)
    updated = await _versioned(s, task_id, version, {"deleted_at": _now(now)})
    await _changed(s, updated, ["deleted"], now)
    return _with_change(updated, await _record(s, actor, row, updated))


async def _restored_column(s: AsyncSession, row: Mapping[Any, Any], values: dict[str, Any]) -> None:
    """Keeps a restored column only while it is a live column of the task's project
    holding the restored status; otherwise the task goes last in the first column that
    holds it."""
    status = Status(values.get("status", row["status"]))
    column_id = values.get("column_id", row["column_id"])
    live = await s.scalar(
        select(_columns.c.id).where(
            _columns.c.id == column_id,
            _columns.c.project_id == row["project_id"],
            _columns.c.status_map == status,
            _live(_columns),
        )
    )
    if live is None:
        values["column_id"], values["board_rank"] = await _slot(s, row["project_id"], status)


async def undo_task(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    change_id: UUID,
    version: int,
    *,
    now: datetime | None = None,
) -> TaskOut:
    """Puts back what change `change_id` of this task changed (R-09, UX 9). A person only
    (403 `session_required` for an agent); the change must belong to the task (404) and
    not be undone yet (409 `already_undone`); `version` must be the task's current one
    and the one this change left (409 `stale_version`: it changed since, so an older
    change never overwrites a newer write). Applies the change's `before` without the
    transition table, recomputes `completed_at` for the restored status, never touches the
    history fields, marks the change undone and records the undo as a change of its own.
    Emits `task.updated` and, when the status comes back, `task.status_changed` with
    `via="undo"`."""
    if actor_kind(actor) is not ActorKind.HUMAN:
        raise ProblemError(403, "session_required", "Only a person can undo a change")
    row = await _row(s, task_id, lock=True, trashed=True)
    change = (
        (
            await s.execute(
                select(_changes)
                .where(_changes.c.change_id == change_id, _changes.c.task_id == task_id)
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if change is None:
        raise NotFound("task_changes", change_id)
    if change["undone_at"] is not None:
        raise ProblemError(409, "already_undone", "This change was already undone")
    if row["version"] != version or change["task_version"] != version:
        raise _stale(row)
    at = _now(now)
    values = rules.restore_values(change["before"], row["completed_at"], at)
    if "status" in values or "column_id" in values:
        await _restored_column(s, row, values)
    updated = (
        (
            await s.execute(
                update(_tasks)
                .where(_tasks.c.id == task_id, _tasks.c.version == version)
                .values(**values)
                .returning(*_tasks.c)
            )
        )
        .mappings()
        .one()
    )  # the row is locked at `version`
    await s.execute(update(_changes).where(_changes.c.id == change["id"]).values(undone_at=at))
    before, _after = rules.change_between(row, updated)
    if before:
        await _changed(s, updated, list(before), now)
    if updated["status"] != row["status"]:
        await emit(
            s,
            TaskStatusChangedV1(
                task_id=task_id,
                from_=Status(row["status"]),
                to=Status(updated["status"]),
                actor=str(actor),
                via="undo",
            ),
            occurred_at=at,
        )
    return _with_change(updated, await _record(s, actor, row, updated))


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


async def list_comments(
    s: AsyncSession, task_id: UUID, *, cursor: str | None = None, limit: int = 50
) -> Page[CommentOut]:
    """The task's comments, oldest first (404 for a task the caller cannot see)."""
    await _row(s, task_id)
    stmt = select(_comments).where(_comments.c.task_id == task_id, _live(_comments))
    return await paginate(
        s, stmt, keys=[], id_col=_comments.c.id, cursor=cursor, limit=limit, model=CommentOut
    )


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


# --- Recurrence (P0-19, FR-3.5) ----------------------------------------------------------------
#
# A recurring task belongs to a rule (`recurrence_rules`): the spec, a template snapshot of
# the task and the latest instance's occurrence. Each instance is a task carrying the rule
# and the local date it stands for (`occurrence_on`); `ux_tasks_ws_rule_occurrence` allows
# one per rule and date. The next instance comes when the latest one is done (in that
# transaction) or once it is overdue and still open (the recurrence tick), each through
# `rules_recurrence.successor` and an INSERT ... ON CONFLICT DO NOTHING on that index, so a
# race between the two makes one row. Instances are Backlog copies of the template, due on
# their local date, with source `recurrence`.

_recurrence: Table = RecurrenceRule.__table__  # type: ignore[assignment]
_day_closes: Table = DayClose.__table__  # type: ignore[assignment]
# What a successor copies from the task the rule was set on (plan: title, label, estimate,
# first action, acceptance criteria, priority, project; plus who set the label and taint).
TEMPLATE_FIELDS: Final = (
    "title",
    "label",
    "label_source",
    "priority",
    "estimate_minutes",
    "first_action",
    "acceptance_criteria",
    "tainted",
)
RECURRENCE_SOURCE: Final = "recurrence"
_JUST_BEFORE: Final = timedelta(microseconds=1)


class RecurrenceIn(BaseModel):
    """`PUT /v1/tasks/{id}/recurrence`: a preset or a 5-field cron (never both), the
    preset's weekday (weekly, 0 = Monday) or month day (monthly), the local due time and the
    task's version. The task becomes the rule's first instance."""

    model_config = ConfigDict(extra="forbid")
    preset: Preset | None = None
    cron: Annotated[str, StringConstraints(max_length=120, strip_whitespace=True)] | None = None
    weekday: int | None = None
    month_day: int | None = None
    due_time: time = time(9, 0)
    version: Version


class RecurrenceOut(BaseModel):
    """A recurrence rule: its spec, the template's title, its latest instance and the next
    occurrence after it (UTC)."""

    id: UUID
    project_id: UUID
    preset: Preset | None
    cron: str | None
    weekday: int | None
    month_day: int | None
    due_time: time
    title: str
    latest_task_id: UUID | None
    latest_occurrence_on: date | None
    next_due_at: datetime | None

    @field_validator("next_due_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class TaskRecurrenceOut(RecurrenceOut):
    """The rule as seen from one of its tasks; `version` is the task's (send it back)."""

    task_id: UUID
    version: int


def _spec(row: Mapping[Any, Any]) -> rr.RecurrenceSpec:
    return rr.RecurrenceSpec(
        preset=None if row["preset"] is None else rr.Preset(row["preset"]),
        cron=row["cron"],
        weekday=row["weekday"],
        month_day=row["month_day"],
        due_time=row["due_time"],
    )


def _invalid(exc: rr.InvalidRecurrence) -> ProblemError:
    return ProblemError(422, exc.code, str(exc))


async def _zone(s: AsyncSession) -> ZoneInfo:
    found = await auth.workspace_timezone(s, _context().workspace_id)
    return ZoneInfo(found.timezone)


def _recurrence_select() -> Select[Any]:
    """Rules with their template title and latest instance (the latest occurrence, trashed
    instances included: the tick treats a trashed one as not done)."""
    latest = (
        select(_tasks.c.id, _tasks.c.occurrence_on)
        .where(_tasks.c.recurrence_rule_id == _recurrence.c.id)
        .order_by(_tasks.c.occurrence_on.desc(), _tasks.c.id.desc())
        .limit(1)
        .lateral("latest")
    )
    return (
        select(
            _recurrence.c.id,
            _recurrence.c.project_id,
            _recurrence.c.preset,
            _recurrence.c.cron,
            _recurrence.c.weekday,
            _recurrence.c.month_day,
            _recurrence.c.due_time,
            _recurrence.c.next_due_at,
            _recurrence.c.task_template["title"].astext.label("title"),
            latest.c.id.label("latest_task_id"),
            latest.c.occurrence_on.label("latest_occurrence_on"),
        )
        .select_from(_recurrence.outerjoin(latest, true()))
        .where(_live(_recurrence))
    )


async def _live_rule(
    s: AsyncSession, rule_id: UUID | None, *, lock: bool = False
) -> RowMapping | None:
    if rule_id is None:
        return None
    stmt = select(_recurrence).where(_recurrence.c.id == rule_id, _live(_recurrence))
    if lock:
        stmt = stmt.with_for_update()
    return (await s.execute(stmt)).mappings().first()


async def _latest_instance(s: AsyncSession, rule_id: UUID) -> RowMapping | None:
    stmt = (
        select(_tasks)
        .where(_tasks.c.recurrence_rule_id == rule_id)
        .order_by(_tasks.c.occurrence_on.desc(), _tasks.c.id.desc())
        .limit(1)
    )
    return (await s.execute(stmt)).mappings().first()


async def _task_recurrence(s: AsyncSession, task: Mapping[Any, Any]) -> TaskRecurrenceOut:
    rule_id = task["recurrence_rule_id"]
    found = (
        (await s.execute(_recurrence_select().where(_recurrence.c.id == rule_id)))
        .mappings()
        .first()
    )
    if rule_id is None or found is None:
        raise NotFound("recurrence_rules", task["id"])
    return TaskRecurrenceOut(**dict(found), task_id=task["id"], version=task["version"])


async def get_recurrence(s: AsyncSession, task_id: UUID) -> TaskRecurrenceOut:
    """The rule of the task; 404 when the task (or a live rule on it) does not exist."""
    return await _task_recurrence(s, await _row(s, task_id))


async def put_recurrence(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    body: RecurrenceIn,
    *,
    now: datetime | None = None,
) -> TaskRecurrenceOut:
    """Sets or changes the task's recurrence at the task's version. The rule snapshots the
    task as its template. When the task is (or becomes) the rule's latest instance, it is
    the first instance: its occurrence is the first one on or after its due date (or after
    now) and its due date becomes that local date. 404, then 409 `stale_version`, then 422
    `invalid_recurrence`."""
    row = await _row(s, task_id, lock=True)  # 404 before any body rule (A0.3, #28)
    if row["version"] != body.version:
        raise _stale(row)
    spec = rr.RecurrenceSpec(
        body.preset, body.cron or None, body.weekday, body.month_day, body.due_time
    )
    at, tz = _now(now), await _zone(s)
    rule = await _live_rule(s, row["recurrence_rule_id"])
    latest = None if rule is None else await _latest_instance(s, rule["id"])
    first_instance = latest is None or latest["id"] == row["id"]
    try:
        rr.validate_spec(spec)
        if first_instance:
            after = at
            if row["due_on"] is not None:
                start = local_to_utc(row["due_on"], time(0), tz)
                after = max(at, start - _JUST_BEFORE)
            occurrence = rr.next_occurrence(spec, after, tz)
        else:
            assert rule is not None  # noqa: S101  # a later instance has a rule
            occurrence = rule["latest_occurrence_at"]
        following = rr.next_occurrence(spec, occurrence, tz)
    except rr.InvalidRecurrence as exc:
        raise _invalid(exc) from None
    values: dict[str, Any] = {
        "preset": spec.preset,
        "cron": spec.cron,
        "weekday": spec.weekday,
        "month_day": spec.month_day,
        "due_time": spec.due_time,
        "task_template": {field: row[field] for field in TEMPLATE_FIELDS},
        "latest_occurrence_at": occurrence,
        "next_due_at": following,
    }
    if rule is None:
        rule_id: UUID = await s.scalar(
            pg_insert(_recurrence)
            .values(project_id=row["project_id"], created_by=actor, **values)
            .returning(_recurrence.c.id)
        )
    else:
        rule_id = rule["id"]
        await s.execute(update(_recurrence).where(_recurrence.c.id == rule_id).values(**values))
    task_values: dict[str, Any] = {"recurrence_rule_id": rule_id}
    if first_instance:
        day = occurrence.astimezone(tz).date()
        task_values |= {"occurrence_on": day, "due_on": day}
    updated = await _versioned(s, task_id, body.version, task_values)
    if updated["due_on"] != row["due_on"]:
        await _changed(s, updated, ["due_on"], now)
    mark_changed(s, LIVE_ENTITY, task_id)
    return await _task_recurrence(s, updated)


async def delete_recurrence(
    s: AsyncSession, actor: ActorRef, task_id: UUID, version: int, *, now: datetime | None = None
) -> None:
    """Stops the recurrence at the task's version: the rule goes to the trash (its
    instances stay) and the task leaves it. 404 without a live rule, 409 `stale_version`."""
    row = await _row(s, task_id, lock=True)
    rule = await _live_rule(s, row["recurrence_rule_id"])
    if rule is None:
        raise NotFound("recurrence_rules", task_id)
    if row["version"] != version:
        raise _stale(row)
    await s.execute(
        update(_recurrence).where(_recurrence.c.id == rule["id"]).values(deleted_at=_now(now))
    )
    await _versioned(s, task_id, version, {"recurrence_rule_id": None, "occurrence_on": None})
    mark_changed(s, LIVE_ENTITY, task_id)


async def list_recurrence(
    s: AsyncSession,
    *,
    project_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = 50,
    project_ids: frozenset[UUID] | None = None,
) -> Page[RecurrenceOut]:
    """Live rules in creation order, optionally of one project (the Schedule rail, P0-24);
    `project_ids` limits them (a project-limited key, R-28)."""
    stmt = _recurrence_select()
    if project_id is not None:
        stmt = stmt.where(_recurrence.c.project_id == project_id)
    if project_ids is not None:
        stmt = stmt.where(_recurrence.c.project_id.in_(project_ids))
    return await paginate(
        s,
        stmt,
        keys=[],
        id_col=_recurrence.c.id,
        cursor=cursor,
        limit=limit,
        model=RecurrenceOut,
    )


async def _create_successor(
    s: AsyncSession, rule: Mapping[Any, Any], occurrence: datetime, tz: ZoneInfo, now: datetime
) -> TaskOut | None:
    """The instance for `occurrence`, or None when one exists already (the unique index
    `ux_tasks_ws_rule_occurrence`: the loser of a race inserts nothing, without error)."""
    day = occurrence.astimezone(tz).date()
    template = rule["task_template"]
    column_id, board_rank = await _slot(s, rule["project_id"], Status.BACKLOG)
    created = (
        (
            await s.execute(
                pg_insert(_tasks)
                .values(
                    **{field: template.get(field) for field in TEMPLATE_FIELDS},
                    project_id=rule["project_id"],
                    status=Status.BACKLOG,
                    due_on=day,
                    recurrence_rule_id=rule["id"],
                    occurrence_on=day,
                    column_id=column_id,
                    board_rank=board_rank,
                    source=RECURRENCE_SOURCE,
                    created_by=SYSTEM_ACTOR,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        _tasks.c.workspace_id,
                        _tasks.c.recurrence_rule_id,
                        _tasks.c.occurrence_on,
                    ],
                    index_where=_tasks.c.recurrence_rule_id.is_not(None),
                )
                .returning(*_tasks.c)
            )
        )
        .mappings()
        .first()
    )
    if created is None:
        return None
    await s.execute(
        update(_recurrence)
        .where(_recurrence.c.id == rule["id"])
        .values(
            latest_occurrence_at=occurrence,
            next_due_at=rr.next_occurrence(_spec(rule), occurrence, tz),
        )
    )
    return await _announce_created(s, created, now)


async def _successor_on_done(s: AsyncSession, task: Mapping[Any, Any], now: datetime) -> None:
    """Done on a rule's latest instance makes the next one (FR-3.5). The board lock, then
    the rule's row lock, before reading the rule: the tick takes them in the same order."""
    await _lock_project_board(s, task["project_id"])
    rule = await _live_rule(s, task["recurrence_rule_id"], lock=True)
    if rule is None or rule["latest_occurrence_at"] is None:
        return
    latest = await _latest_instance(s, rule["id"])
    if latest is None or latest["id"] != task["id"]:
        return  # an earlier instance: its successor exists already
    tz = await _zone(s)
    occurrence = rr.successor(_spec(rule), rule["latest_occurrence_at"], True, "done", now, tz)
    if occurrence is not None:
        await _create_successor(s, rule, occurrence, tz, now)


async def create_due_successors(s: AsyncSession, now: datetime) -> int:
    """The recurrence tick for the workspace in context: every live rule whose latest
    instance is due and still open gets its next instance (one, however many occurrences
    were missed). Returns how many were created. Each rule is read again under its
    project's board lock and its row lock (the order completion takes them, in a fixed
    project order), so a completion committing meanwhile is seen, not raced."""
    tz = await _zone(s)
    listed = (
        await s.execute(
            select(_recurrence.c.id, _recurrence.c.project_id)
            .where(_live(_recurrence))
            .order_by(_recurrence.c.project_id, _recurrence.c.id)
        )
    ).all()
    created = 0
    for rule_id, project_id in listed:
        await _lock_project_board(s, project_id)
        rule = await _live_rule(s, rule_id, lock=True)
        if rule is None:
            continue  # stopped meanwhile
        latest = await _latest_instance(s, rule["id"])
        if latest is None or rule["latest_occurrence_at"] is None:
            continue
        done = latest["status"] == Status.DONE and latest["deleted_at"] is None
        occurrence = rr.successor(_spec(rule), rule["latest_occurrence_at"], done, "tick", now, tz)
        if occurrence is not None and await _create_successor(s, rule, occurrence, tz, now):
            created += 1
    return created


# --- Day close (P0-19, FR-3.6) -----------------------------------------------------------------


class DayCloseFacts(BaseModel):
    """What the day-close tick needs from a workspace: its zone and the anchor (the later of
    the last close and the last timezone change, REL-6)."""

    timezone: str
    anchor: datetime


async def day_close_facts(s: AsyncSession) -> DayCloseFacts:
    zone = await auth.workspace_timezone(s, _context().workspace_id)
    last: datetime | None = await s.scalar(select(func.max(_day_closes.c.closed_at)))
    anchor = zone.changed_at if last is None else max(last, zone.changed_at)
    return DayCloseFacts(timezone=zone.timezone, anchor=anchor)


async def roll_over_today(s: AsyncSession, day: date, now: datetime) -> int | None:
    """Closes local `day` for the workspace in context: Today tasks go back to Backlog
    through the state machine as the system (`rollover_count` + 1, `task.status_changed`
    each) and one `day_closes` row records it. None, changing nothing, when the day was
    closed already (a repeat or a replay)."""
    closed: UUID | None = await s.scalar(
        pg_insert(_day_closes)
        .values(day=day, closed_at=now, rolled_over=0, created_by=SYSTEM_ACTOR)
        .on_conflict_do_nothing(index_elements=[_day_closes.c.workspace_id, _day_closes.c.day])
        .returning(_day_closes.c.id)
    )
    if closed is None:
        return None
    rows = (
        (
            await s.execute(
                select(_tasks)
                .where(_tasks.c.status == Status.TODAY, _live(_tasks))
                .order_by(_tasks.c.id)
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        await _transition(s, SYSTEM_ACTOR, row, Status.BACKLOG, row["version"], now=now)
    await s.execute(
        update(_day_closes).where(_day_closes.c.id == closed).values(rolled_over=len(rows))
    )
    return len(rows)


# --- Trash purge (P0-19, REL-6) ----------------------------------------------------------------


async def purge_trash(s: AsyncSession, cutoff: datetime, *, limit: int) -> int:
    """Hard-deletes up to `limit` tasks of the workspace in context trashed before `cutoff`
    (their comments, context links and undo log go with them, ON DELETE CASCADE). Subtasks go first,
    and a task with a subtask that stays is kept. Returns how many went."""
    child = _tasks.alias("child")
    kept_child = (
        select(child.c.id)
        .where(
            child.c.parent_id == _tasks.c.id,
            or_(child.c.deleted_at.is_(None), child.c.deleted_at >= cutoff),
        )
        .exists()
    )
    batch = (
        select(_tasks.c.id)
        .where(_tasks.c.deleted_at < cutoff, ~kept_child)
        .order_by(_tasks.c.parent_id.is_(None), _tasks.c.id)
        .limit(limit)
        .scalar_subquery()
    )
    result = await s.execute(delete(_tasks).where(_tasks.c.id.in_(batch)).returning(_tasks.c.id))
    return len(result.all())
