"""tasks MCP tools; thin calls into api.py (P2-01, FR-14.10, R-10, R-31, R-33).

Four ops, each an MCP tool and a REST twin in router.py (`surface.rest_twin`):

| Tool | Scope | REST twin |
| --- | --- | --- |
| `list_tasks` | tasks:read | `GET /v1/tasks` |
| `create_task` | tasks:write | `POST /v1/tasks` |
| `update_task_status` | tasks:write | `POST /v1/tasks/{task_id}/status` |
| `update_estimate` | tasks:write | `POST /v1/tasks/{task_id}/estimate` |

Each `*Body` model is the twin's body (or query); the tool's input adds the path
parameter and, for writes, `idempotency_key`. Writes answer the task with its subtask
`layout`; a task written by a key with no run is tainted (R-31).
"""

from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from tumnis.core import agent_surface as surface
from tumnis.core.pagination import LIMIT_DEFAULT, LIMIT_MAX
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.versioning import Version
from tumnis.modules.tasks import api

# --- Inputs ----------------------------------------------------------------------------------


class ListTasksIn(surface.SurfaceInput):
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX)
    project_id: UUID | None = None
    status: api.Status | None = None
    label: api.Label | None = None
    parent_id: UUID | None = None
    order: api.TaskOrder = "created"


class CreateTaskBody(surface.SurfaceInput):
    project_id: UUID
    parent_id: UUID | None = None  # a subtask; the server lays it out against the threshold
    title: api.Title
    label: api.Label | None = None  # None = pending (R-08)
    priority: api.Priority = "normal"
    due_on: date | None = None
    # Minutes of human time; required for a Human or Hybrid task an agent creates.
    estimate_minutes: api.Estimate | None = None
    first_action: api.LongText | None = None
    acceptance_criteria: api.LongText | None = None
    status: Literal["backlog", "today"] = "backlog"


class CreateTaskIn(surface.WriteInput, CreateTaskBody):
    pass


class StatusBody(surface.SurfaceInput):
    to: api.Status
    version: Version


class UpdateTaskStatusIn(surface.UpdateInput, StatusBody):
    task_id: UUID


Reason = Annotated[str, StringConstraints(min_length=1, max_length=500, strip_whitespace=True)]


class EstimateBody(surface.SurfaceInput):
    estimate_minutes: api.Estimate
    reason: Reason  # why the estimate changed; logged with the change
    version: Version


class UpdateEstimateIn(surface.UpdateInput, EstimateBody):
    task_id: UUID


# --- Handlers --------------------------------------------------------------------------------


def _fields(data: BaseModel, *extra: str) -> dict[str, Any]:
    return data.model_dump(exclude={"schema_version", "idempotency_key", *extra})


async def _list(call: surface.SurfaceCall, data: ListTasksIn) -> api.TaskPage:
    return await api.list_tasks(
        call.session,
        project_id=data.project_id,
        status=data.status,
        label=data.label,
        parent_id=data.parent_id,
        order=data.order,
        cursor=data.cursor,
        limit=data.limit,
        project_ids=call.project_ids,
    )


async def _create(call: surface.SurfaceCall, data: CreateTaskIn) -> api.TaskWithLayoutOut:
    created = await api.create_task(
        call.session,
        call.actor,
        api.TaskCreate(**_fields(data)),
        now=call.now,
        tainted=call.tainted,
    )
    return await api.with_layout(call.session, created)


async def _status(call: surface.SurfaceCall, data: UpdateTaskStatusIn) -> api.TaskWithLayoutOut:
    moved = await api.change_status(
        call.session, call.actor, data.task_id, data.to, data.version, now=call.now
    )
    return await api.with_layout(call.session, moved)


async def _estimate(call: surface.SurfaceCall, data: UpdateEstimateIn) -> api.TaskWithLayoutOut:
    updated = await api.update_estimate(
        call.session,
        call.actor,
        data.task_id,
        data.estimate_minutes,
        data.reason,
        data.version,
        now=call.now,
    )
    return await api.with_layout(call.session, updated)


async def _project_of_task(ctx: WorkspaceContext, raw: Any) -> UUID | None:
    try:
        task_id = UUID(str(raw.get("task_id")))
    except ValueError:
        return None
    return await api.project_of(ctx, task_id)


# --- Ops -------------------------------------------------------------------------------------

LIST_TASKS = surface.register_op(
    surface.SurfaceOp(
        name="list_tasks",
        description=(
            "List tasks, oldest first, optionally filtered by project, status, label or parent"
            " task. Pages by cursor; `total` counts every match."
        ),
        scope="tasks:read",
        input_model=ListTasksIn,
        output_model=api.TaskPage,
        rest_method="GET",
        rest_path="/v1/tasks",
        write=False,
        updates_existing=False,
        project_arg="project_id",
        project_resolver=None,
        handler=_list,
    )
)
CREATE_TASK = surface.register_op(
    surface.SurfaceOp(
        name="create_task",
        description=(
            "Create a task or, with parent_id, a subtask in a project. Human and Hybrid tasks"
            " need estimate_minutes (minutes of human time); AI tasks carry none. A subtask"
            " is laid out against the project's card threshold (layout)."
        ),
        scope="tasks:write",
        input_model=CreateTaskIn,
        output_model=api.TaskWithLayoutOut,
        rest_method="POST",
        rest_path="/v1/tasks",
        write=True,
        updates_existing=False,
        project_arg="project_id",
        project_resolver=None,
        handler=_create,
    )
)
UPDATE_TASK_STATUS = surface.register_op(
    surface.SurfaceOp(
        name="update_task_status",
        description=(
            "Move a task to another status at the version you read. Agents may take only"
            " the agent transitions (for example, never Backlog to Done)."
        ),
        scope="tasks:write",
        input_model=UpdateTaskStatusIn,
        output_model=api.TaskWithLayoutOut,
        rest_method="POST",
        rest_path="/v1/tasks/{task_id}/status",
        write=True,
        updates_existing=True,
        project_arg=None,
        project_resolver=_project_of_task,
        handler=_status,
    )
)
UPDATE_ESTIMATE = surface.register_op(
    surface.SurfaceOp(
        name="update_estimate",
        description=(
            "Re-estimate a Human or Hybrid task (minutes of human time) at the version you"
            " read, with the reason for the change."
        ),
        scope="tasks:write",
        input_model=UpdateEstimateIn,
        output_model=api.TaskWithLayoutOut,
        rest_method="POST",
        rest_path="/v1/tasks/{task_id}/estimate",
        write=True,
        updates_existing=True,
        project_arg=None,
        project_resolver=_project_of_task,
        handler=_estimate,
    )
)
