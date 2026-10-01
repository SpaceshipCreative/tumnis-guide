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
- `result.posted` (P2-04): an agent's result for its run is stored and the task is In
  review: the result's id, run, task, project, outcome, summary and links.
- `task.commented` (P2-03): a comment added, with its author and text, for the digest.
- `context_item.linked` (P2-03): a task newly linked to a ContextItem, for the digest.
- `review_item.added` (P1-13): a new review item, by id, kind and target only (never the
  payload); decisions asks Jev how much it blocks.

`doc` holds the title, the body (first action, acceptance criteria and comments, capped at
8 KB, plan default), whether the task is deleted, and (P0-20, additive) the task's project
and when the change happened: search keeps the newest doc it has seen per task.
"""

from datetime import datetime
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
    project_id: UUID | None = None  # P0-20
    updated_at: datetime | None = None  # P0-20: the change's time (the event's occurred_at)


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
    # set when an undo put the status back (P0-24); left out of the payload otherwise
    via: Literal["undo"] | None = Field(default=None, exclude_if=lambda v: v is None)


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


@event_type("task.commented", 1)
class TaskCommentedV1(EventPayload):
    """A comment on a task (P2-03: the digest carries it). `author` is the ActorRef that
    wrote it (`user:<id>` for a person); the text is the comment's Markdown."""

    event_name: ClassVar[str] = "task.commented"
    schema_version: Literal[1] = 1
    task_id: UUID
    project_id: UUID
    comment_id: UUID
    author: str
    text: str


@event_type("context_item.linked", 1)
class ContextItemLinkedV1(EventPayload):
    """A task newly linked to outside content through a ContextItem (P2-03: the digest
    carries the item's full text, read when the digest is read)."""

    event_name: ClassVar[str] = "context_item.linked"
    schema_version: Literal[1] = 1
    context_item_id: UUID
    task_id: UUID
    project_id: UUID
    target_type: str
    target_id: UUID | None = None


@event_type("review_item.added", 1)
class ReviewItemAddedV1(EventPayload):
    event_name: ClassVar[str] = "review_item.added"
    schema_version: Literal[1] = 1
    item_id: UUID
    kind: str
    project_id: UUID | None
    target_type: str
    target_id: UUID


class PostedLink(BaseModel):
    """A link an agent's result names (branch, pull request, document, draft or url)."""

    kind: str = Field(max_length=40)
    url: str = Field(max_length=2048)
    label: str | None = Field(default=None, max_length=200)


@event_type("result.posted", 1)
class ResultPostedV1(EventPayload):
    """An agent's result for its run is stored and its task is In review (P2-04, FR-5.8)."""

    event_name: ClassVar[str] = "result.posted"
    schema_version: Literal[1] = 1
    result_id: UUID
    run_id: UUID
    task_id: UUID
    project_id: UUID | None
    outcome: Literal["done", "partial", "blocked"]
    summary: str = Field(max_length=20_000)
    links: list[PostedLink] = Field(default=[], max_length=50)
