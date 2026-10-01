"""focus event payload models (P2-15, A8, R-06). They live apart from `events.py` so `api.py`
can emit them while `events.py` calls `api.py` (the tasks module's pattern).

- `focus.event`: a focus event fired (FR-10.2), with the level and rule that produced it
  (FR-10.9) and the message shown. The only output of P2-15; the bar reads it over `/ws`
  and P2-16 delivers it further.
- `focus.responded`: a one-tap answer to a focus event (FR-10.4), "less of this" included;
  it joins the task's project digest (P2-03).
- `focus.level_changed`: the workspace's focus level (`scope` "workspace") or today's
  override (`scope` "today") changed; it joins the workspace digest (P2-03).
"""

from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from tumnis.core.events import EventPayload, event_type

Level = Literal["quiet", "nudge", "coach", "guardrail"]
EventKind = Literal[
    "block_start", "not_started", "check_in_due", "switched", "stuck", "block_end", "day_end"
]
Response = Literal["still_on_it", "switched", "stuck", "snooze", "less_of_this"]


@event_type("focus.event", 1)
class FocusEventV1(EventPayload):
    event_name: ClassVar[str] = "focus.event"
    schema_version: Literal[1] = 1
    event_id: UUID
    kind: EventKind
    task_id: UUID | None
    rule: str
    level: Level
    message: str
    fired_at: datetime


@event_type("focus.responded", 1)
class FocusRespondedV1(EventPayload):
    event_name: ClassVar[str] = "focus.responded"
    schema_version: Literal[1] = 1
    event_id: UUID
    task_id: UUID | None
    session_id: UUID | None
    response: Response


@event_type("focus.level_changed", 1)
class FocusLevelChangedV1(EventPayload):
    model_config = ConfigDict(
        validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    event_name: ClassVar[str] = "focus.level_changed"
    schema_version: Literal[1] = 1
    from_: Level = Field(alias="from")
    to: Level
    scope: Literal["workspace", "today"]
