"""tasks event payload models (P0-18, A8, R-06, R-07); `events.py` re-exports them with
the module's subscribers. They live apart so `api.py` can emit them while `events.py`
calls `api.py` (no import cycle inside the module).

- `task.created`: a new task, with what search indexes (`doc`), since search may not call
  tasks (boundary rule 4). Also the label (None while pending), who made it (`source`) and
  its taint.
- `task.updated`: fields changed (`changed_fields`, sorted; `comments` when a comment was
  added) and the task's `doc` after the change (R-06: later WPs only add fields).
- `task.status_changed`: `from`, `to` and the actor (an ActorRef: `user:<id>`,
  `api_key:<id>`, `system`, ...); `via` is `undo` when an undo put the status back (P0-24).
- `human.decided`: a decision on a review item (R-07's superset). The payload is fixed here;
  P1-13 and P2-05 emit it.

`doc` holds the title, the body (first action, acceptance criteria and comments, capped at
8 KB, plan default) and whether the task is deleted.
"""

from typing import Annotated, Any, ClassVar, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tumnis.core.events import EventPayload, event_type
from tumnis.modules.tasks.rules import Label, Status

DOC_BODY_MAX_BYTES: Final = 8_192  # plan default: 8 KB


class TaskDoc(BaseModel):
    """What search indexes for a task."""

    title: str
    body: Annotated[str, StringConstraints(max_length=DOC_BODY_MAX_BYTES)] = ""
    deleted: bool = False


@event_type("task.created", 1)
class TaskCreatedV1(EventPayload):
    event_name: ClassVar[str] = "task.created"
    schema_version: Literal[1] = 1
    task_id: UUID
    project_id: UUID
    label: Label | None
    source: str
    tainted: bool
    doc: TaskDoc


@event_type("task.updated", 1)
class TaskUpdatedV1(EventPayload):
    event_name: ClassVar[str] = "task.updated"
    schema_version: Literal[1] = 1
    task_id: UUID
    changed_fields: list[str]
    doc: TaskDoc


@event_type("task.status_changed", 1)
class TaskStatusChangedV1(EventPayload):
    model_config = ConfigDict(
        validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    event_name: ClassVar[str] = "task.status_changed"
    schema_version: Literal[1] = 1
    task_id: UUID
    from_: Status = Field(alias="from")
    to: Status
    actor: str
    via: Literal["undo"] | None = None  # set when an undo put the status back (P0-24)


@event_type("human.decided", 1)
class HumanDecidedV1(EventPayload):
    event_name: ClassVar[str] = "human.decided"
    schema_version: Literal[1] = 1
    item_kind: str
    item_id: UUID
    target_type: str
    target_id: UUID
    decision: str
    reason: str | None = None
    previous: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    decision_id: UUID | None = None
