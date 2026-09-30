"""tasks FastAPI router: `/v1/tasks…`, `/v1/projects/{id}/columns`, `/v1/projects/{id}/board`
and `/v1/review/…`; thin calls into api.py (P0-18, FR-3.1, FR-3.2, FR-3.4, R-03, R-20).

Tasks, board and columns take a session or a key (`tasks:read` to read, `tasks:write` to
write; agents write tasks, A10). A project-limited key reaches only its projects: routes on
a task resolve the task's project (`lookup:tasks`), the others name it in the path, query
or body (R-28). Writes are idempotent; versioned ones answer 409 `stale_version` with the
current task, a refused transition 409 `transition_not_allowed`. The review badge and the
kind registry are for the signed-in app only.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from tumnis.core.clock import Clock
from tumnis.core.idempotency import SessionDep
from tumnis.core.pagination import Page, PageParams, page_params
from tumnis.core.principal import principal_of
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.versioning import Version
from tumnis.modules.tasks import api

router = v1_router("tasks", tags=["tasks"])

READ = frozenset({"tasks:read"})
WRITE_SCOPE = frozenset({"tasks:write"})
LIST = RoutePolicy(
    auth="session_or_key", scopes=READ, paginated=True, project_param="query:project_id"
)
READ_TASK = RoutePolicy(auth="session_or_key", scopes=READ, project_param="lookup:tasks")
WRITE_TASK = RoutePolicy(
    auth="session_or_key", scopes=WRITE_SCOPE, idempotent=True, project_param="lookup:tasks"
)
CREATE = RoutePolicy(
    auth="session_or_key", scopes=WRITE_SCOPE, idempotent=True, project_param="body:project_id"
)
READ_PROJECT = RoutePolicy(auth="session_or_key", scopes=READ, project_param="path:project_id")
WRITE_PROJECT = RoutePolicy(
    auth="session_or_key", scopes=WRITE_SCOPE, idempotent=True, project_param="path:project_id"
)
SESSION_READ = RoutePolicy(auth="session")
LIST_PULL_REQUESTS = RoutePolicy(
    auth="session_or_key",
    scopes=READ,
    project_param="lookup:tasks",
    unpaginated_reason="a task links a handful of pull requests",
)
LIST_OF_TASK = RoutePolicy(
    auth="session_or_key", scopes=READ, paginated=True, project_param="lookup:tasks"
)
# Undo is for people (R-09): a key or token gets 403 `session_required`.
UNDO = RoutePolicy(auth="session", idempotent=True)


class StatusIn(BaseModel):
    to: api.Status
    version: Version


class MoveIn(BaseModel):
    """One board drag (R-20): the target column, the rank between the new neighbours'
    ranks (core/rank.py) and the version read."""

    column_id: UUID
    board_rank: api.BoardRank
    version: Version


class TrashIn(BaseModel):
    version: Version


class UndoIn(BaseModel):
    """The change a write answered (`change_id`) and the version it left (R-09)."""

    change_id: UUID
    version: Version


class CommentIn(BaseModel):
    body_md: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]


class ContextItemIn(BaseModel):
    context_item_id: UUID


class PullRequestIn(BaseModel):
    url: Annotated[str, StringConstraints(min_length=1, max_length=2048, strip_whitespace=True)]


class ColumnsIn(BaseModel):
    columns: Annotated[list[api.ColumnIn], Field(min_length=1, max_length=30)]


class ReviewCountOut(BaseModel):
    count: int


class ReviewKindOut(BaseModel):
    kind: str
    owner_module: str
    actions: list[str]
    impact_scope: str
    payload_schema: dict[str, object]


class ReviewKindsOut(BaseModel):
    items: list[ReviewKindOut]


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


# --- Tasks -----------------------------------------------------------------------------------


@router.get("/tasks")
@route_policy(LIST)
async def list_tasks(
    request: Request,
    session: SessionDep,
    *,
    page: Annotated[PageParams, Depends(page_params)],
    project_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[api.Status | None, Query()] = None,
    order: Annotated[api.TaskOrder, Query()] = "created",
) -> api.TaskPage:
    """Tasks, optionally of one project and one status, and how many match (`total`).
    `order=created` (default) is creation order; `order=today` is the Today order
    (priority, then due date, then created time; P0-23)."""
    return await api.list_tasks(
        session,
        project_id=project_id,
        status=status,
        order=order,
        cursor=page.cursor,
        limit=page.limit,
        project_ids=principal_of(request).project_ids,
    )


@router.post("/tasks", status_code=201)
@route_policy(CREATE)
async def create_task(body: api.TaskCreate, request: Request, session: SessionDep) -> api.TaskOut:
    return await api.create_task(
        session, principal_of(request).actor, body, now=_clock(request).now()
    )


@router.get("/tasks/{task_id}")
@route_policy(READ_TASK)
async def get_task(task_id: UUID, session: SessionDep) -> api.TaskOut:
    return await api.get_task(session, task_id)


@router.patch("/tasks/{task_id}")
@route_policy(WRITE_TASK)
async def update_task(
    task_id: UUID, body: api.TaskPatch, request: Request, session: SessionDep
) -> api.TaskOut:
    return await api.update_task(
        session, principal_of(request).actor, task_id, body, body.version, now=_clock(request).now()
    )


@router.post("/tasks/{task_id}/status")
@route_policy(WRITE_TASK)
async def change_status(
    task_id: UUID, body: StatusIn, request: Request, session: SessionDep
) -> api.TaskOut:
    """Moves the task through the state machine (FR-3.2)."""
    return await api.change_status(
        session,
        principal_of(request).actor,
        task_id,
        body.to,
        body.version,
        now=_clock(request).now(),
    )


@router.post("/tasks/{task_id}/move")
@route_policy(WRITE_TASK)
async def move_task(
    task_id: UUID, body: MoveIn, request: Request, session: SessionDep
) -> api.TaskOut:
    """One board drag: column, rank and version in one request (R-20)."""
    return await api.move_task(
        session,
        principal_of(request).actor,
        task_id,
        body.column_id,
        body.board_rank,
        body.version,
        now=_clock(request).now(),
    )


@router.delete("/tasks/{task_id}")
@route_policy(WRITE_TASK)
async def trash_task(
    task_id: UUID, body: TrashIn, request: Request, session: SessionDep
) -> api.TaskOut:
    """Moves the task to the trash (UX 9); `POST /undo` with the answered `change_id`
    brings it back."""
    return await api.trash_task(
        session, principal_of(request).actor, task_id, body.version, now=_clock(request).now()
    )


@router.post("/tasks/{task_id}/undo")
@route_policy(UNDO)
async def undo_task(
    task_id: UUID, body: UndoIn, request: Request, session: SessionDep
) -> api.TaskOut:
    """Puts back what one change did (R-09, UX 9): 409 `already_undone`, or
    `stale_version` when the task changed since."""
    return await api.undo_task(
        session,
        principal_of(request).actor,
        task_id,
        body.change_id,
        body.version,
        now=_clock(request).now(),
    )


@router.get("/tasks/{task_id}/comments")
@route_policy(LIST_OF_TASK)
async def list_comments(
    task_id: UUID,
    session: SessionDep,
    page: Annotated[PageParams, Depends(page_params)],
) -> Page[api.CommentOut]:
    """The task's comments, oldest first."""
    return await api.list_comments(session, task_id, cursor=page.cursor, limit=page.limit)


@router.post("/tasks/{task_id}/comments", status_code=201)
@route_policy(WRITE_TASK)
async def add_comment(
    task_id: UUID, body: CommentIn, request: Request, session: SessionDep
) -> api.CommentOut:
    return await api.add_comment(
        session, principal_of(request).actor, task_id, body.body_md, now=_clock(request).now()
    )


@router.post("/tasks/{task_id}/context-items", status_code=201)
@route_policy(WRITE_TASK)
async def link_context_item(
    task_id: UUID, body: ContextItemIn, request: Request, session: SessionDep
) -> api.TaskContextItemOut:
    """Links outside content by ContextItem id (FR-14.2); linking again keeps one link."""
    return await api.link_context_item(
        session,
        principal_of(request).actor,
        task_id,
        body.context_item_id,
        now=_clock(request).now(),
    )


@router.get("/tasks/{task_id}/pull-requests")
@route_policy(LIST_PULL_REQUESTS)
async def list_pull_requests(
    task_id: UUID, request: Request, session: SessionDep
) -> list[api.PullRequestOut]:
    """The task's pull requests with their stored status. Opening the task asks for a fresh
    read: the worker's answer arrives over `/ws` as a change of the task (FR-12.1)."""
    return await api.pull_requests(session, task_id, now=_clock(request).now())


@router.post("/tasks/{task_id}/pull-requests", status_code=201)
@route_policy(WRITE_TASK)
async def link_pull_request(
    task_id: UUID, body: PullRequestIn, request: Request, session: SessionDep
) -> api.PullRequestOut:
    """Links a github.com pull request (422 `not_a_pull_request`, 422 `repo_not_allowed`
    outside Settings > GitHub's allow-list); linking again keeps one link (FR-12.1)."""
    return await api.link_pull_request(
        session, principal_of(request).actor, task_id, body.url, now=_clock(request).now()
    )


# --- Recurrence (P0-19, FR-3.5) --------------------------------------------------------------


@router.get("/tasks/{task_id}/recurrence")
@route_policy(READ_TASK)
async def get_recurrence(task_id: UUID, session: SessionDep) -> api.TaskRecurrenceOut:
    """The task's recurrence rule; 404 when it has none."""
    return await api.get_recurrence(session, task_id)


@router.put("/tasks/{task_id}/recurrence")
@route_policy(WRITE_TASK)
async def put_recurrence(
    task_id: UUID, body: api.RecurrenceIn, request: Request, session: SessionDep
) -> api.TaskRecurrenceOut:
    """Sets or changes the recurrence at the task's version (a preset or a cron, 422
    `invalid_recurrence`); the task becomes the first instance."""
    return await api.put_recurrence(
        session, principal_of(request).actor, task_id, body, now=_clock(request).now()
    )


@router.delete("/tasks/{task_id}/recurrence", status_code=204)
@route_policy(WRITE_TASK)
async def delete_recurrence(
    task_id: UUID,
    request: Request,
    session: SessionDep,
    *,
    version: Annotated[Version, Query()],
) -> Response:
    """Stops the recurrence at the task's version; the instances made so far stay."""
    await api.delete_recurrence(
        session, principal_of(request).actor, task_id, version, now=_clock(request).now()
    )
    return Response(status_code=204)


@router.get("/recurrence")
@route_policy(LIST)
async def list_recurrence(
    request: Request,
    session: SessionDep,
    *,
    page: Annotated[PageParams, Depends(page_params)],
    project_id: Annotated[UUID | None, Query()] = None,
) -> Page[api.RecurrenceOut]:
    """Recurrence rules, optionally of one project (the Schedule rail)."""
    return await api.list_recurrence(
        session,
        project_id=project_id,
        cursor=page.cursor,
        limit=page.limit,
        project_ids=principal_of(request).project_ids,
    )


# --- Board and columns -----------------------------------------------------------------------


@router.get("/projects/{project_id}/board")
@route_policy(READ_PROJECT)
async def get_board(project_id: UUID, session: SessionDep) -> api.BoardOut:
    """The kanban: columns, cards and each card's checklist (FR-3.4)."""
    return await api.board(session, project_id)


@router.get("/projects/{project_id}/columns")
@route_policy(READ_PROJECT)
async def get_columns(project_id: UUID, session: SessionDep) -> api.ColumnsOut:
    return await api.list_columns(session, project_id)


@router.put("/projects/{project_id}/columns")
@route_policy(WRITE_PROJECT)
async def put_columns(
    project_id: UUID, body: ColumnsIn, request: Request, session: SessionDep
) -> api.ColumnsOut:
    """Renames, reorders, adds and removes columns in one request; every status keeps at
    least one column (422 `status_without_column`)."""
    return await api.put_columns(
        session, principal_of(request).actor, project_id, body.columns, now=_clock(request).now()
    )


# --- Review ----------------------------------------------------------------------------------


@router.get("/review/count")
@route_policy(SESSION_READ)
async def get_review_count(request: Request, session: SessionDep) -> ReviewCountOut:
    """The badge: review items waiting now (not decided, trashed or snoozed)."""
    return ReviewCountOut(count=await api.review_badge_count(session, _clock(request).now()))


@router.get("/review/kinds")
@route_policy(SESSION_READ)
async def list_review_kinds() -> ReviewKindsOut:
    """The registered review kinds and their actions (R-03, R-05)."""
    return ReviewKindsOut(
        items=[
            ReviewKindOut(
                kind=spec.kind,
                owner_module=spec.owner_module,
                actions=list(spec.actions),
                impact_scope=spec.impact_scope,
                payload_schema=spec.payload_schema.model_json_schema(),
            )
            for spec in sorted(api.review_kinds().values(), key=lambda spec: spec.kind)
        ]
    )
