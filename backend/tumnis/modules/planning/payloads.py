"""planning event payload models (P1-11, A8, R-06). They live apart from `events.py` so
`api.py` can emit them while `events.py` calls `api.py` (the tasks module's pattern).

- `plan.published`: a day's plan was published (morning, Re-plan or the due-date
  fallback): its tasks in order with their reasons, and where it came from (`source`:
  master or fallback). Notifications and focus events read it; it is emitted once per
  plan, in the plan's own transaction.
"""

from datetime import date
from typing import ClassVar, Literal
from uuid import UUID

from tumnis.core.events import EventPayload, event_type


@event_type("plan.published", 1)
class PlanPublishedV1(EventPayload):
    event_name: ClassVar[str] = "plan.published"
    schema_version: Literal[1] = 1
    plan_id: UUID
    day: date
    task_ids: list[UUID]
    reasons: list[str]
    source: Literal["master", "fallback", "manual"]
    trigger: Literal["morning", "replan", "manual"]
