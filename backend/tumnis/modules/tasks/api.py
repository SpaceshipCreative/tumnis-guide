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

from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
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
from sqlalchemy.engine import ScalarResult
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
from tumnis.modules.github import api as github
from tumnis.modules.integrations import api as integrations
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import rules
from tumnis.modules.tasks import rules_recurrence as rr
from tumnis.modules.tasks.models import (
    BoardColumn,
    DayClose,
    RecurrenceRule,
    Result,
    Task,
    TaskChange,
    TaskComment,
    TaskContextItem,
)
from tumnis.modules.tasks.payloads import (
    DOC_BODY_MAX_BYTES,
    ContextItemLinkedV1,
    HumanDecidedV1,
    PostedLink,
    ResultPostedV1,
    TaskCommentedV1,
    TaskCreatedV1,
    TaskDoc,
    TaskStatusChangedV1,
    TaskUpdatedV1,
)
from tumnis.modules.tasks.review import (
    ESTIMATE_KIND,
    LABEL_KIND,
    Deciding,
    DuplicateReviewKind,
    EstimateOutlierPayload,
    ImpactFacts,
    LowConfidenceLabelPayload,
    ReviewAction,
    ReviewItemOut,
    ReviewKindSpec,
    TargetRef,
    UnknownReviewKind,
    add_review_item,
    close_open_items,
    decide_review_item,
    flag_pull_request_results,
    get_review_item,
    list_review_items,
    refresh_review_impact,
    refresh_review_impact_of_task,
    register_review_kind,
    review_badge_count,
    review_impact_facts,
    review_kinds,
    set_review_jev,
    validate_decision,
    validate_payload,
)
from tumnis.modules.tasks.rules import (
    ActorKind,
    Label,
    LabelSource,
    LabelState,
    Status,
    jev_factor,
    label_state,
    may_auto_label,
)
from tumnis.modules.tasks.rules_recurrence import Preset
from tumnis.seed import TaskSeed, register_seed_writer

__all__ = [
    "ESTIMATE_KIND",
    "LABEL_KIND",
    "ActorKind",
    "Deciding",
    "DuplicateReviewKind",
    "EstimateOutlierPayload",
    "HumanDecidedV1",
    "ImpactFacts",
    "Label",
    "LabelSource",
    "LabelState",
    "LowConfidenceLabelPayload",
    "ReviewAction",
    "ReviewItemOut",
    "ReviewKindSpec",
    "Status",
    "TargetRef",
    "UnknownReviewKind",
    "add_review_item",
    "decide_review_item",
    "get_review_item",
    "jev_factor",
    "label_state",
    "list_review_items",
    "may_auto_label",
    "refresh_review_impact",
    "refresh_review_impact_of_task",
    "register_review_kind",
    "review_badge_count",
    "review_impact_facts",
    "review_kinds",
    "set_review_jev",
    "validate_decision",
    "validate_payload",
]

_tasks: Table = Task.__table__  # type: ignore[assignment]
_columns: Table = BoardColumn.__table__  # type: ignore[assignment]
_comments: Table = TaskComment.__table__  # type: ignore[assignment]
_links: Table = TaskContextItem.__table__  # type: ignore[assignment]
_changes: Table = TaskChange.__table__  # type: ignore[assignment]
_results: Table = Result.__table__  # type: ignore[assignment]
_log = structlog.get_logger(__name__)

LIVE_ENTITY: Final = "task"
PROJECT_ENTITY: Final = "project"  # column edits refresh the project's views
Priority = Literal["low", "normal", "high", "urgent"]
TaskOrder = Literal["created", "today"]
Title = Annotated[str, StringConstraints(min_length=1, max_length=500, strip_whitespace=True)]
Estimate = Annotated[int, Field(gt=0, le=MAX_ESTIMATE_MINUTES)]
LongText = Annotated[str, StringConstraints(max_length=8_000)]
FirstActionSource = Literal["placeholder", "agent"]  # P1-08; NULL: a person's or none
EnrichmentStatus = Literal[
    "pending", "running", "done", "agent_offline", "not_provisioned", "failed"
]
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
    # P1-07: the one-line reason for the label (or the suggestion), and a low-confidence
    # suggestion shown while `label` stays pending (R-08).
    label_reason: str | None
    label_suggestion: Label | None
    status: Status
    priority: Priority
    due_on: date | None
    estimate_minutes: int | None
    first_action: str | None
    # P1-08: `placeholder` (the Generation slot's stand-in while the project agent works)
    # or `agent` (its enrichment); null for a first action a person wrote, or none.
    first_action_source: str | None = None  # FirstActionSource (the column CHECK holds it)
    acceptance_criteria: str | None
    # P1-08: the project agent's enrichment of the task; null before one starts.
    enrichment_status: str | None = None  # EnrichmentStatus (the column CHECK holds it)
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


Layout = Literal["card", "checklist", "nested_ai"]


class TaskWithLayoutOut(TaskOut):
    """A task as the agent surface answers it (P2-01): where a subtask shows against its
    project's card threshold (`card`, a `checklist` item on its parent's card, or
    `nested_ai` for AI work); null for a root task."""

    layout: Layout | None = None


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


# --- Results (P2-04, FR-5.8) ------------------------------------------------------------------

ResultOutcome = Literal["done", "partial", "blocked"]
ResultUrl = Annotated[str, StringConstraints(max_length=2048, pattern=r"^https?://\S+$")]


class FileTouched(BaseModel):
    path: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    change: Literal["added", "modified", "deleted"]


class ResultLink(BaseModel):
    kind: Literal["branch", "pull_request", "document", "draft", "url"]
    url: ResultUrl
    label: Annotated[str, StringConstraints(max_length=200)] | None = None


class ResultFields(BaseModel):
    """What an agent reports it did in a run."""

    outcome: ResultOutcome
    summary: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    files_touched: list[FileTouched] = Field(default=[], max_length=500)
    links: list[ResultLink] = Field(default=[], max_length=50)
    tests_summary: Annotated[str, StringConstraints(max_length=20_000)] | None = None


class ResultOut(ResultFields):
    id: UUID
    run_id: UUID
    task_id: UUID
    created_at: datetime
    tainted: bool = False  # posted by a tainted run or a key with no run (P2-08, SAF-1)


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


AI_LABEL_SOURCES: Final = frozenset({"jev", "fallback"})


async def get_task(s: AsyncSession, task_id: UUID) -> TaskOut:
    """The task. While its label is still the AI's own write (P1-07), or its latest write
    is the project agent's enrichment (P1-08), `change_id` names that write, so the open
    UI can offer to undo it for the session (UX 9)."""
    row = await _row(s, task_id)
    change_id = None
    if row["label_source"] in AI_LABEL_SOURCES or row["enrichment_status"] == "done":
        change_id = await s.scalar(
            select(_changes.c.change_id)
            .where(
                _changes.c.task_id == task_id,
                _changes.c.task_version == row["version"],
                _changes.c.undone_at.is_(None),
                _changes.c.actor == str(SYSTEM_ACTOR),
            )
            .order_by(_changes.c.change_id.desc())
            .limit(1)
        )
    return _with_change(row, change_id)


async def list_tasks(
    s: AsyncSession,
    *,
    project_id: UUID | None = None,
    status: Status | None = None,
    order: TaskOrder = "created",
    cursor: str | None = None,
    limit: int = 50,
    project_ids: frozenset[UUID] | None = None,
    label: Label | None = None,
    parent_id: UUID | None = None,
) -> TaskPage:
    """Live tasks, optionally of one project or one status, with the filter's `total`.
    `label` and `parent_id` narrow further (the `list_tasks` tool, P2-01).
    `order="created"` is creation order; `order="today"` is `rules.today_order` (priority,
    then due date with undated last, then oldest, then id) as keyset keys, so the pages
    walk the same order. `project_ids` limits them (a project-limited key, R-28)."""
    stmt = select(_tasks).where(_live(_tasks))
    if project_id is not None:
        stmt = stmt.where(_tasks.c.project_id == project_id)
    if status is not None:
        stmt = stmt.where(_tasks.c.status == status)
    if label is not None:
        stmt = stmt.where(_tasks.c.label == label)
    if parent_id is not None:
        stmt = stmt.where(_tasks.c.parent_id == parent_id)
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


async def open_tasks(s: AsyncSession) -> list[TaskOut]:
    """Every live task that is not Done, in creation order: one statement, for the daily
    plan's candidates (P1-11)."""
    rows = await s.execute(
        select(_tasks)
        .where(_live(_tasks), _tasks.c.status != Status.DONE)
        .order_by(_tasks.c.created_at, _tasks.c.id)
    )
    return [_out(row._mapping) for row in rows]


async def tasks_by_ids(s: AsyncSession, ids: Collection[UUID]) -> list[TaskOut]:
    """The live tasks among `ids` (any status), in creation order: one statement (the
    daily plan's read, P1-11)."""
    rows = await s.execute(
        select(_tasks)
        .where(_live(_tasks), _tasks.c.id.in_(list(ids)))
        .order_by(_tasks.c.created_at, _tasks.c.id)
    )
    return [_out(row._mapping) for row in rows]


async def live_task_ids(s: AsyncSession, ids: Collection[UUID]) -> set[UUID]:
    """The ids among `ids` whose task is live (not trashed, not purged): one statement,
    even for no ids, so callers keep a fixed statement count (the Calendar week, P1-12)."""
    rows = await s.execute(select(_tasks.c.id).where(_live(_tasks), _tasks.c.id.in_(list(ids))))
    return {row.id for row in rows}


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
    if "label" in before:  # the label's metadata goes back with it on undo (P1-07)
        before |= _label_meta(before_row)
        after |= _label_meta(after_row)
    if "first_action" in before:  # and where the first action came from (P1-08)
        before[FIRST_ACTION_SOURCE] = before_row[FIRST_ACTION_SOURCE]
        after[FIRST_ACTION_SOURCE] = after_row[FIRST_ACTION_SOURCE]
    return await record_change(s, actor, after_row["id"], before, after)


# What goes with a label on undo (P1-07, R-09): who set it, why, how sure, the decision
# behind it and a suggestion it replaced.
_LABEL_META: Final = (
    "label_source",
    "label_reason",
    "label_confidence",
    "label_decision_id",
    "label_suggestion",
)


FIRST_ACTION_SOURCE: Final = "first_action_source"  # goes back with the first action (P1-08)


def _label_meta(row: Mapping[Any, Any]) -> dict[str, object]:
    """The row's label metadata as JSON values, for a change row."""
    return {f: str(row[f]) if isinstance(row[f], UUID) else row[f] for f in _LABEL_META}


def _restored_meta(before: Mapping[str, Any]) -> dict[str, Any]:
    """The label metadata a change's `before` holds, as row values."""
    values = {f: before[f] for f in _LABEL_META if f in before}
    if isinstance(values.get("label_decision_id"), str):
        values["label_decision_id"] = UUID(values["label_decision_id"])
    return values


def _with_change(row: Mapping[Any, Any], change_id: UUID | None) -> TaskOut:
    return _out(row).model_copy(update={"change_id": change_id})


# --- Writing ---------------------------------------------------------------------------------


def _estimate(label: Label | None, estimate: int | None, kind: ActorKind) -> int | None:
    try:
        return rules.normalize_estimate(label, estimate, kind)
    except rules.EstimateRequired as exc:
        raise ProblemError(422, exc.code, str(exc)) from None


async def _check_parent(s: AsyncSession, parent_id: UUID, project_id: UUID) -> RowMapping:
    """A subtask's parent is a live root task of the same project (depth one, plan
    default): 404 when there is no such task, 422 `parent_project_mismatch` or
    `parent_not_root` otherwise. Answers the parent's row."""
    parent = await _row(s, parent_id)
    if parent["project_id"] != project_id:
        raise ProblemError(
            422, "parent_project_mismatch", "A subtask belongs to its parent's project"
        )
    if parent["parent_id"] is not None:
        raise ProblemError(422, "parent_not_root", "A subtask cannot have subtasks")
    return parent


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


# The run task counter (P2-09, SAF-5): agents counts a task created with a run's task
# token against the run's limit, in the caller's transaction, and raises 409
# `run_limit_exceeded` past it. agents registers it at import (tasks cannot import agents:
# agents imports tasks).
RunTaskCounter = Callable[..., Awaitable[None]]
_run_task_counter: list[RunTaskCounter] = []


def register_run_task_counter(counter: RunTaskCounter) -> None:
    """Set the counter `create_task` calls for a task made by a run's task token; called
    as `counter(session, run_id, now=...)`."""
    _run_task_counter[:] = [counter]


async def create_task(  # the task, plus where it came from
    s: AsyncSession,
    actor: ActorRef,
    data: TaskCreate,
    *,
    now: datetime | None = None,
    source: str | None = None,
    label_source: LabelSource | None = None,
    tainted: bool = False,
    context_item_ids: Sequence[UUID] = (),
    run_id: UUID | None = None,
) -> TaskOut:
    """A task in Backlog or Today, last in its column; emits `task.created`. 404 for a
    project, parent or context item the caller cannot see; 422 `estimate_required` (an
    agent's Human or Hybrid task without an estimate), `parent_project_mismatch`,
    `parent_not_root`. An AI task's estimate is dropped. `label_source` defaults from the
    actor (user, agent, fallback); `source` says where the task came from (default: user,
    agent or system). `context_item_ids` are linked in the same transaction.

    Taint (P2-08, SAF-1) is derived once and stored: the OR of the linked context items,
    the parent task and the caller (`tainted`: a tainted run's token, or a key with no run,
    R-31).

    `run_id` (a run's task token, P2-09) counts the task against the run's tasks-per-run
    limit (SAF-5); past it the call is 409 `run_limit_exceeded` and nothing is created."""
    await _require_project(s, data.project_id)
    if run_id is not None:
        for counter in _run_task_counter:
            await counter(s, run_id, now=_now(now))
    sources = [rules.TaintSource("user", None, tainted=False)]
    if tainted:
        sources.append(rules.TaintSource("run", None, tainted=True))
    if data.parent_id is not None:
        parent = await _check_parent(s, data.parent_id, data.project_id)
        sources.append(rules.TaintSource("parent_task", parent["id"], tainted=parent["tainted"]))
    items = [await _context_item(s, item_id) for item_id in dict.fromkeys(context_item_ids)]
    sources += [rules.TaintSource("context_item", i.id, tainted=i.tainted) for i in items]
    kind = actor_kind(actor)
    values = data.model_dump(exclude={"schema_version"})
    values["estimate_minutes"] = _estimate(data.label, data.estimate_minutes, kind)
    values["label_source"] = None if data.label is None else (label_source or _LABEL_SOURCE[kind])
    values["tainted"] = rules.derive_taint(sources)
    created = await _insert(s, actor, values, now=now, source=source or _SOURCE[kind])
    for item in items:
        await _link(s, actor, created.id, data.project_id, item, now=now)
    return created


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
    label_override: bool = True,
) -> TaskOut:
    """Changes the fields the patch sets at `version`; emits `task.updated` with the names
    of the fields whose value changed (R-06). Setting a label records who set it
    (`label_source`); the estimate rule applies whenever the label or estimate changes.

    A person changing a label the AI decided (P1-07: `label_decision_id` set or a
    `label_suggestion` waiting, and not already the user's) is a recorded human decision:
    `human.decided` with `item_kind = label_override` (R-07) and the open
    `low_confidence_label` items of the task closed as `superseded`. A caller that already
    records the decision another way passes `label_override=False`."""
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
    if values.get("label") is not None:  # a person's (or agent's) label ends the suggestion
        values["label_suggestion"] = None
    if "first_action" in values:  # written here: no longer the placeholder or the agent's
        values[FIRST_ACTION_SOURCE] = None
    changed = [field for field, value in values.items() if row[field] != value]
    updated = await _versioned(s, task_id, version, values or {"updated_at": func.now()})
    if changed:
        await _changed(s, updated, changed, now)
    # Setting the AI's own label makes it the person's too (only `label_source` changes).
    if label_override and kind is ActorKind.HUMAN and {"label", "label_source"} & set(changed):
        await _label_overridden(s, row, updated, now)
    return _with_change(updated, await _record(s, actor, row, updated))


async def _label_overridden(
    s: AsyncSession, row: Mapping[Any, Any], updated: Mapping[Any, Any], now: datetime | None
) -> None:
    """`human.decided` for a person's label over the AI's (P1-07, R-07), and the task's
    open low-confidence items closed as superseded."""
    ai_decided = row["label_decision_id"] is not None or row["label_suggestion"] is not None
    if not ai_decided or row["label_source"] == "user" or updated["label"] is None:
        return
    at = _now(now)
    chosen = str(updated["label"])
    proposed = row["label_suggestion"] or row["label"]
    await emit(
        s,
        HumanDecidedV1(
            item_kind="label_override",
            item_id=row["id"],
            target_type="task",
            target_id=row["id"],
            decision=chosen,
            previous={
                "label": row["label"],
                "label_source": row["label_source"],
                "label_suggestion": row["label_suggestion"],
            },
            payload={"value": chosen, "overridden": chosen != proposed},
            decision_id=row["label_decision_id"],
        ),
        occurred_at=at,
    )
    await close_open_items(
        s, LABEL_KIND, TargetRef(type="task", id=row["id"]), decision="superseded", at=at
    )


# --- AI labels (P1-07, FR-4.1, R-08) ---------------------------------------------------------


def _retitled(row: RowMapping, for_title: str | None) -> bool:
    """Whether the task's title is no longer the one the AI's answer was decided for."""
    return for_title is not None and row["title"] != for_title


async def set_ai_label(
    s: AsyncSession,
    task_id: UUID,
    *,
    label: Label,
    source: Literal["jev", "fallback"],
    reason: str,
    confidence: float | None,
    decision_id: UUID,
    for_title: str | None = None,
    now: datetime | None = None,
) -> UUID | None:
    """The AI's label, unless a person chose one first: the row is locked and updated only
    while `label_source` is not `user`, so a human override committed while the decision
    ran always wins (None, nothing written). With `for_title` (the title the label was
    decided for), nothing is written once the title has changed: the retitle's own label
    is on its way (FR-4.1). Clears any suggestion, bumps the version,
    records the change (R-09, undoable by a person) and returns its id; emits
    `task.updated` with `label`, `label_reason` and `label_source` and marks the task
    changed for /ws."""
    row = await _row(s, task_id, lock=True)
    if not may_auto_label(row["label_source"]) or _retitled(row, for_title):
        return None
    values = {
        "label": label,
        "label_source": source,
        "label_reason": reason,
        "label_confidence": confidence,
        "label_decision_id": decision_id,
        "label_suggestion": None,
    }
    updated = (
        (
            await s.execute(
                update(_tasks)
                .where(
                    _tasks.c.id == task_id,
                    _live(_tasks),
                    _tasks.c.label_source.is_distinct_from("user"),
                )
                .values(**values)
                .returning(*_tasks.c)
            )
        )
        .mappings()
        .first()
    )
    if updated is None:
        return None
    await _changed(s, updated, ["label", "label_reason", "label_source"], now)
    return await record_change(
        s,
        SYSTEM_ACTOR,
        task_id,
        {"label": row["label"], **_label_meta(row)},
        {"label": str(label), **_label_meta(updated)},
    )


async def set_label_suggestion(
    s: AsyncSession,
    task_id: UUID,
    *,
    suggestion: Label,
    reason: str | None,
    confidence: float | None,
    decision_id: UUID,
    probabilities: Mapping[str, float] | None = None,
    for_title: str | None = None,
    now: datetime | None = None,
) -> bool:
    """A low-confidence label: kept as `label_suggestion` (the label stays pending, R-08)
    with one open `low_confidence_label` review item for the human, unless a person has
    chosen the label, or the title is no longer `for_title` (False, nothing written).
    An AI label already on the task (a retitle's relabel) goes back to pending, so no
    confirmed label stands beside the suggestion's reason; that change is recorded (R-09).
    The review item replaces any open one for the task, which closes as `superseded`.
    Emits `task.updated` with `label_suggestion` (and `label`, `label_source` when an AI
    label was cleared)."""
    row = await _row(s, task_id, lock=True)
    if not may_auto_label(row["label_source"]) or _retitled(row, for_title):
        return False
    values: dict[str, Any] = {
        "label_suggestion": suggestion,
        "label_reason": reason,
        "label_confidence": confidence,
        "label_decision_id": decision_id,
    }
    cleared = row["label"] is not None
    if cleared:
        values |= {"label": None, "label_source": None}
    updated = await _versioned(s, task_id, row["version"], values)
    await _changed(
        s,
        updated,
        ["label", "label_source", "label_suggestion"] if cleared else ["label_suggestion"],
        now,
    )
    if cleared:
        await _record(s, SYSTEM_ACTOR, row, updated)
    target = TargetRef(type="task", id=task_id)
    await close_open_items(s, LABEL_KIND, target, decision="superseded", at=_now(now))
    await add_review_item(
        LABEL_KIND,
        target=target,
        project_id=row["project_id"],
        payload=LowConfidenceLabelPayload(
            suggested=suggestion,
            probabilities=dict(probabilities or {}),
            reason=reason,
            decision_id=decision_id,
        ).model_dump(mode="json"),
        dedupe_key=f"label:{task_id}",
        session=s,
    )
    return True


# --- Enrichment by the project agent (P1-08, FR-4.4, FR-4.6, UX 9) ----------------------------


class EnrichmentWrite(BaseModel):
    """What an enrichment writes (agents' merge decided it): None leaves a field alone."""

    first_action: LongText | None = None
    acceptance_criteria: LongText | None = None
    estimate_minutes: Estimate | None = None
    label: Label | None = None
    label_reason: Annotated[str, StringConstraints(max_length=200)] | None = None


def _blank(text: str | None) -> bool:
    return text is None or not text.strip()


async def set_enrichment_status(
    s: AsyncSession,
    task_id: UUID,
    status: EnrichmentStatus,
    *,
    placeholder: str | None = None,
    now: datetime | None = None,
) -> bool:
    """The enrichment's status, and the Generation slot's `placeholder` first action
    (`first_action_source = "placeholder"`) when given and the task still has none, in one
    write (every write bumps the version, so they share it). A placeholder is nobody's
    change: it records no `task_changes` row, and `task.updated` names it; a status alone
    only refreshes the live views. False (nothing written) for a task that is gone."""
    try:
        row = await _row(s, task_id, lock=True)
    except NotFound:
        return False
    values: dict[str, Any] = {"enrichment_status": status}
    written = placeholder is not None and _blank(row["first_action"])
    if written:
        values |= {"first_action": placeholder, FIRST_ACTION_SOURCE: "placeholder"}
    if not written and row["enrichment_status"] == status:
        return True
    updated = await _versioned(s, task_id, row["version"], values)
    if written:
        await _changed(s, updated, ["first_action", FIRST_ACTION_SOURCE, "enrichment_status"], now)
    else:
        mark_changed(s, LIVE_ENTITY, task_id)
    return True


async def apply_enrichment(
    s: AsyncSession,
    task_id: UUID,
    write: EnrichmentWrite,
    version: int,
    *,
    now: datetime | None = None,
) -> UUID | None:
    """The project agent's enrichment at `version` (409 `stale_version` when the task
    changed since it was read: read, merge and write again): one versioned write with
    `enrichment_status = "done"`, `first_action_source = "agent"` with a first action,
    `label_source = "agent"` with a revised label (which drops an estimate when it is AI,
    and closes an open `low_confidence_label` item as superseded, since it clears the
    suggestion that item asked about). One `task_changes` row by the system
    (R-09), undone through `POST /v1/tasks/{id}/undo`, whose id this returns (None when the
    write filled nothing); `task.updated` names the changed fields (R-06)."""
    row = await _row(s, task_id, lock=True)
    values: dict[str, Any] = {"enrichment_status": "done"}
    if write.first_action is not None:
        values |= {"first_action": write.first_action, FIRST_ACTION_SOURCE: "agent"}
    if write.acceptance_criteria is not None:
        values["acceptance_criteria"] = write.acceptance_criteria
    if write.estimate_minutes is not None:
        values["estimate_minutes"] = write.estimate_minutes
    if write.label is not None:
        values |= {
            "label": write.label,
            "label_source": "agent",
            "label_reason": write.label_reason,
            "label_confidence": None,
            "label_decision_id": None,
            "label_suggestion": None,
        }
        if write.label is Label.AI:  # AI work carries no estimate (normalize_estimate)
            values["estimate_minutes"] = None
    changed = [field for field, value in values.items() if row[field] != value]
    updated = await _versioned(s, task_id, version, values)
    if changed:
        await _changed(s, updated, changed, now)
    if write.label is not None:  # the suggestion it answered is gone with its review item
        await close_open_items(
            s, LABEL_KIND, TargetRef(type="task", id=task_id), decision="superseded", at=_now(now)
        )
    return await _record(s, SYSTEM_ACTOR, row, updated)


async def add_estimate_outlier(
    s: AsyncSession, task_id: UUID, payload: EstimateOutlierPayload
) -> None:
    """One open `estimate_outlier` review item for the task (P1-08, FR-11.4): accept keeps
    the estimate, edit sets another."""
    row = await _row(s, task_id)
    await add_review_item(
        ESTIMATE_KIND,
        target=TargetRef(type="task", id=task_id),
        project_id=row["project_id"],
        payload=payload.model_dump(mode="json"),
        dedupe_key=f"{ESTIMATE_KIND}:{task_id}",
        session=s,
    )


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


async def subtask_layout(s: AsyncSession, task: TaskOut) -> Layout | None:
    """Where the subtask shows (FR-3.4, FR-3.8): `rules.subtask_placement` against the
    project's effective threshold; AI work always nests. None for a root task."""
    if task.parent_id is None:
        return None
    label = _label(task.label)
    if label is Label.AI:
        return "nested_ai"
    threshold = await projects.effective_subtask_threshold(s, task.project_id)
    placement = rules.subtask_placement(label, task.estimate_minutes, threshold)
    return "card" if placement is rules.Placement.CARD else "checklist"


async def with_layout(s: AsyncSession, task: TaskOut) -> TaskWithLayoutOut:
    return TaskWithLayoutOut(**task.model_dump(), layout=await subtask_layout(s, task))


async def update_estimate(  # noqa: PLR0917  # the tool's input, plus who and when
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    estimate_minutes: int,
    reason: str,
    version: int,
    *,
    now: datetime | None = None,
) -> TaskOut:
    """Re-estimates a Human or Hybrid task at `version` (the `update_estimate` tool,
    P2-01): 422 `estimate_not_applicable` for AI work or a pending label, which carry no
    estimate of human time. The reason is logged beside the change, once it is made."""
    row = await _row(s, task_id)  # 404 before any body rule (A0.3, #28)
    label = _label(row["label"])
    if label not in (Label.HUMAN, Label.HYBRID):
        raise ProblemError(
            422,
            "estimate_not_applicable",
            "Only Human and Hybrid tasks carry an estimate of human time",
        )
    patch = TaskPatch(estimate_minutes=estimate_minutes, version=version)
    updated = await update_task(s, actor, task_id, patch, version, now=now)
    _log.info(
        "tasks.estimate_updated",
        task_id=str(task_id),
        before=row["estimate_minutes"],
        after=updated.estimate_minutes,
        reason=reason,
        actor=str(actor),
        version=updated.version,
        change_id=str(updated.change_id) if updated.change_id else None,
    )
    return updated


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
    if "label" in values:
        values |= _restored_meta(change["before"])
    if "first_action" in values:
        values[FIRST_ACTION_SOURCE] = change["before"].get(FIRST_ACTION_SOURCE)
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
    """A markdown comment on the task; `task.updated` carries `comments` and the new doc,
    and `task.commented` the comment itself (P2-03)."""
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
    await emit(
        s,
        TaskCommentedV1(
            task_id=task_id,
            project_id=row["project_id"],
            comment_id=created["id"],
            author=str(actor),
            text=body_md,
        ),
        occurred_at=_now(now),
    )
    return CommentOut.model_validate(dict(created))


async def result_of_run(s: AsyncSession, run_id: UUID) -> ResultOut | None:
    """The run's stored result, None before one was posted."""
    found = (
        (await s.execute(select(_results).where(_results.c.run_id == run_id, _live(_results))))
        .mappings()
        .first()
    )
    return None if found is None else ResultOut.model_validate(dict(found))


async def post_result(  # the result, plus who and when
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    run_id: UUID,
    fields: ResultFields,
    *,
    tainted: bool = False,
    now: datetime | None = None,
) -> tuple[ResultOut, bool]:
    """Stores the run's result (P2-04, FR-5.8) in the caller's transaction: the `results`
    row, the task In progress -> In review as `actor` (an agent), and `result.posted`.
    Once per run: a second call answers the first result and `False` (nothing changes).
    A task no longer In progress (a human moved it) keeps its status."""
    existing = await result_of_run(s, run_id)
    if existing is not None:
        return existing, False
    row = await _row(s, task_id, lock=True)
    created = (
        (
            await s.execute(
                pg_insert(_results)
                .values(
                    task_id=task_id,
                    run_id=run_id,
                    tainted=tainted,
                    **fields.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "run_id"])
                .returning(*_results.c)
            )
        )
        .mappings()
        .first()
    )
    if created is None:  # a concurrent post won
        again = await result_of_run(s, run_id)
        assert again is not None  # noqa: S101  # the conflict names a live row
        return again, False
    at = _now(now)
    if Status(row["status"]) is Status.IN_PROGRESS:
        await _transition(s, actor, row, Status.IN_REVIEW, row["version"], now=at)
    await emit(
        s,
        ResultPostedV1(
            result_id=created["id"],
            run_id=run_id,
            task_id=task_id,
            project_id=row["project_id"],
            outcome=fields.outcome,
            summary=fields.summary,
            links=[
                PostedLink.model_validate(link.model_dump(mode="json")) for link in fields.links
            ],
        ),
        occurred_at=at,
    )
    return ResultOut.model_validate(dict(created)), True


async def list_comments(
    s: AsyncSession, task_id: UUID, *, cursor: str | None = None, limit: int = 50
) -> Page[CommentOut]:
    """The task's comments, oldest first (404 for a task the caller cannot see)."""
    await _row(s, task_id)
    stmt = select(_comments).where(_comments.c.task_id == task_id, _live(_comments))
    return await paginate(
        s, stmt, keys=[], id_col=_comments.c.id, cursor=cursor, limit=limit, model=CommentOut
    )


async def _context_item(s: AsyncSession, context_item_id: UUID) -> integrations.ContextItemOut:
    item = await integrations.get_context_item_ref(_context(), context_item_id, session=s)
    if item is None:
        raise NotFound("context_items", context_item_id)
    return item


async def raise_taint(s: AsyncSession, task_id: UUID, *, now: datetime | None = None) -> bool:
    """Taints the task if it is not tainted yet (`rules.raise_only`: taint only rises);
    True when it changed. The change is not undoable: `tainted` is not an undo field, so
    `undo_task` never lowers it. Emits `task.updated` (`tainted`) for the projections."""
    raised = (
        (
            await s.execute(
                update(_tasks)
                .where(_tasks.c.id == task_id, _tasks.c.tainted.is_(False))
                .values(tainted=True)
                .returning(*_tasks.c)
            )
        )
        .mappings()
        .first()
    )
    if raised is None:
        return False
    await _changed(s, raised, ["tainted"], now)
    return True


async def _raise_owner_taint(s: AsyncSession, task_id: UUID) -> None:
    """integrations' hook: a tainted item was attached to the task (owner `task`)."""
    await raise_taint(s, task_id)


integrations.register_owner_taint("task", _raise_owner_taint)


async def _link(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    project_id: UUID,
    item: integrations.ContextItemOut,
    *,
    now: datetime | None = None,
) -> UUID:
    """The live link row (made, or restored from a deleted one); its id. A new (or
    restored) link emits `context_item.linked`, so the digest carries the item (P2-03)."""
    context_item_id = item.id
    already: UUID | None = await s.scalar(
        select(_links.c.id).where(
            _links.c.task_id == task_id,
            _links.c.context_item_id == context_item_id,
            _links.c.deleted_at.is_(None),
        )
    )
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
    if already is None:
        await emit(
            s,
            ContextItemLinkedV1(
                context_item_id=context_item_id,
                task_id=task_id,
                project_id=project_id,
                target_type=item.target_type,
                target_id=item.target_id,
            ),
            occurred_at=_now(now),
        )
    return link_id


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
    link; a new (or restored) link emits `context_item.linked` (P2-03). 404 for a task or
    context item the caller cannot see. A tainted item taints the task (`rules.raise_only`,
    P2-08)."""
    row = await _row(s, task_id)
    item = await _context_item(s, context_item_id)
    link_id = await _link(s, actor, task_id, row["project_id"], item, now=now)
    if rules.raise_only(row["tainted"], item.tainted) != row["tainted"]:
        await raise_taint(s, task_id, now=now)
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


async def unlink_context_item(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    context_item_id: UUID,
    *,
    now: datetime | None = None,
) -> None:
    """Removes the task's link to the item (a no-op when there is none). The task keeps
    its taint: unlinking never lowers it (P2-08, SAF-1). 404 for a task the caller
    cannot see."""
    del actor  # who unlinked is the row's updater (app.current_actor)
    await _row(s, task_id)
    await s.execute(
        update(_links)
        .where(
            _links.c.task_id == task_id,
            _links.c.context_item_id == context_item_id,
            _live(_links),
        )
        .values(deleted_at=_now(now))
    )
    mark_changed(s, LIVE_ENTITY, task_id)


async def context_item_ids(s: AsyncSession, task_id: UUID) -> list[UUID]:
    """The context items linked to the task, oldest link first (P2-02's task packet; 404
    for a task the caller cannot see)."""
    await _row(s, task_id)
    rows: ScalarResult[UUID] = await s.scalars(
        select(_links.c.context_item_id)
        .where(_links.c.task_id == task_id, _live(_links))
        .order_by(_links.c.created_at, _links.c.id)
    )
    return list(rows)


class EstimateSample(BaseModel):
    """A finished Human or Hybrid task's estimate beside its actual time (P2-02); its
    title too, cut to ESTIMATE_TITLE_CHARS, for the enrichment request (P1-08)."""

    task_id: UUID
    title: str = ""
    label: Label
    estimate_minutes: int
    actual_minutes: int


ESTIMATE_TITLE_CHARS: Final = 120  # skill_io.EstimateHistoryItem's title limit


ESTIMATE_HISTORY_LIMIT: Final = 10  # finished tasks a task packet carries (plan default)


async def estimate_history(
    s: AsyncSession, project_id: UUID, *, limit: int = ESTIMATE_HISTORY_LIMIT
) -> list[EstimateSample]:
    """The project's most recently finished Human and Hybrid tasks that have both an
    estimate and an actual time, newest first (the task packet's estimate history)."""
    rows = await s.execute(
        select(
            _tasks.c.id,
            _tasks.c.title,
            _tasks.c.label,
            _tasks.c.estimate_minutes,
            _tasks.c.actual_minutes,
        )
        .where(
            _tasks.c.project_id == project_id,
            _live(_tasks),
            _tasks.c.label.in_(("human", "hybrid")),
            _tasks.c.completed_at.is_not(None),
            _tasks.c.estimate_minutes.is_not(None),
            _tasks.c.actual_minutes.is_not(None),
        )
        .order_by(_tasks.c.completed_at.desc(), _tasks.c.id.desc())
        .limit(limit)
    )
    return [
        EstimateSample(
            task_id=row.id,
            title=row.title[:ESTIMATE_TITLE_CHARS],
            label=row.label,
            estimate_minutes=row.estimate_minutes,
            actual_minutes=row.actual_minutes,
        )
        for row in rows
    ]


# --- Pull requests (P2-13, FR-12.1) -----------------------------------------------------------

PullRequestOut = github.PullRequestOut


async def link_pull_request(
    s: AsyncSession, actor: ActorRef, task_id: UUID, url: str, *, now: datetime
) -> PullRequestOut:
    """Links a GitHub pull request to the task: the URL becomes an Artifact (github's
    `track_pull_request`) and the task links it as a ContextItem, so it reaches the task
    the way every outside object does (FR-14.2). 422 `not_a_pull_request` for a URL that is
    not a github.com pull request (GitHub Enterprise included), 422 `repo_not_allowed`
    outside the allow-list in Settings > GitHub; then nothing is stored and GitHub is not
    called. The status is read by the worker (`pull_requests`)."""
    await _row(s, task_id)
    try:
        pull = await github.track_pull_request(_context(), url, now=now, session=s)
    except github.NotAPullRequest:
        raise ProblemError(
            422, "not_a_pull_request", "That is not a github.com pull request URL"
        ) from None
    except github.RepoNotAllowed:
        raise ProblemError(
            422, "repo_not_allowed", "That repository is not on the GitHub allow-list"
        ) from None
    item = await integrations.link_context(
        _context(),
        owner_type="task",
        owner_id=task_id,
        target_type="artifact",
        target_id=pull.artifact_id,
        added_by=actor,
        session=s,
    )
    await link_context_item(s, actor, task_id, item.id, now=now)
    return pull


async def pull_requests(s: AsyncSession, task_id: UUID, *, now: datetime) -> list[PullRequestOut]:
    """The task's pull requests with the status last stored (state, checks, review; None
    before the first read). Opening a task is what asks for a fresh read: each stale one
    is queued for the worker (`github.request_refresh`), and the result arrives over `/ws`
    as a change of the task."""
    await _row(s, task_id)
    ctx = _context()
    targets = await integrations.context_targets(
        ctx, owner_type="task", owner_id=task_id, target_type="artifact", session=s
    )
    found = await github.pull_requests(ctx, targets, session=s)
    for pull in found:
        await github.request_refresh(ctx, pull, now=now)
    return found


async def flag_red_checks(s: AsyncSession, key: str, *, red: bool) -> int:
    """`tasks.flag_red_checks`: the open result review items linking pull request `key`
    get the `checks_red` flag while its checks are red, and lose it when they are not."""
    return await flag_pull_request_results(s, key, red=red)


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
        "acceptance_criteria": rec.acceptance_criteria,
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


# --- Close the day and local metrics (P1-18) ---------------------------------------------------


class DayTaskFacts(BaseModel):
    """A task as the close-the-day panel and the local metrics read it (planning, P1-18)."""

    task_id: UUID
    project_id: UUID
    title: str
    label: Label | None
    status: Status
    completed_at: datetime | None
    rollover_count: int
    estimate_minutes: int | None
    actual_minutes: int | None
    result_posted_at: datetime | None  # the latest result posted within the window


async def day_task_facts(s: AsyncSession, start: datetime, end: datetime) -> list[DayTaskFacts]:
    """The live tasks of the workspace in context that were completed within [start, end),
    are in Today now, or had a result posted within [start, end): one statement, in
    creation order."""
    posted = (
        select(_results.c.task_id, func.max(_results.c.created_at).label("posted"))
        .where(_live(_results), _results.c.created_at >= start, _results.c.created_at < end)
        .group_by(_results.c.task_id)
        .subquery()
    )
    rows = await s.execute(
        select(
            _tasks.c.id,
            _tasks.c.project_id,
            _tasks.c.title,
            _tasks.c.label,
            _tasks.c.status,
            _tasks.c.completed_at,
            _tasks.c.rollover_count,
            _tasks.c.estimate_minutes,
            _tasks.c.actual_minutes,
            posted.c.posted,
        )
        .outerjoin(posted, posted.c.task_id == _tasks.c.id)
        .where(
            _live(_tasks),
            or_(
                and_(
                    _tasks.c.status == Status.DONE,
                    _tasks.c.completed_at >= start,
                    _tasks.c.completed_at < end,
                ),
                _tasks.c.status == Status.TODAY,
                posted.c.posted.is_not(None),
            ),
        )
        .order_by(_tasks.c.created_at, _tasks.c.id)
    )
    return [
        DayTaskFacts(
            task_id=row.id,
            project_id=row.project_id,
            title=row.title,
            label=_label(row.label),
            status=Status(row.status),
            completed_at=row.completed_at,
            rollover_count=row.rollover_count,
            estimate_minutes=row.estimate_minutes,
            actual_minutes=row.actual_minutes,
            result_posted_at=row.posted,
        )
        for row in rows
    ]
