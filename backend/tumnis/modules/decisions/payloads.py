"""decisions event and review payload models (P1-02); `events.py` re-exports them with the
module's subscriber. They live apart so `api.py` can emit them while `events.py` calls
`api.py` (no import cycle inside the module), as in tasks.

- `decision.made`: one per decision, with ids and the outcome only, never the inputs
  (Data flow rule 6); usage counts it.
- `DecisionUnavailablePayload`: the payload of the `decision_unavailable` review item a
  decision gets when no provider answered (FR-11.3); `DecisionValueEdit` is its `edit`
  action's payload (P1-13).
"""

from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel

from tumnis.core.events import EventPayload, event_type
from tumnis.modules.decisions.catalog import DecisionPoint
from tumnis.modules.decisions.rules import Route


@event_type("decision.made", 1)
class DecisionMadeV1(EventPayload):
    event_name: ClassVar[str] = "decision.made"
    schema_version: Literal[1] = 1
    decision_id: UUID
    decision_point: DecisionPoint
    outcome: Route
    provider: Literal["jev", "vllm", "fake", "none"]
    fallback: bool
    cached: bool


class DecisionUnavailablePayload(BaseModel):
    """What the reviewer needs to ask again or set the value by hand."""

    decision_id: UUID
    point: DecisionPoint


class DecisionValueEdit(BaseModel):
    """The `edit` on a `decision_unavailable` item (P1-13): the value the human sets by hand
    (a label, a project key, a yes or no, a score); `decisions.record_outcome` logs it."""

    value: str | int | float | bool
