"""integrations event payload models (P2-13); `events.py` re-exports them. They live apart so
`api.py` can emit them without importing `events.py`.

- `artifact.updated`: an Artifact's state or checks changed (a pull request went red, a
  deployment finished). The module that reads the outside system (github, coolify) writes
  the status through `api.set_artifact_status`, which emits this only when the state or
  the checks differ from what was stored; `tasks` flags review items on it.
"""

from typing import Any, ClassVar, Literal
from uuid import UUID

from tumnis.core.events import EventPayload, event_type


@event_type("artifact.updated", 1)
class ArtifactUpdatedV1(EventPayload):
    event_name: ClassVar[str] = "artifact.updated"
    schema_version: Literal[1] = 1
    artifact_id: UUID
    kind: str
    url: str | None
    state: str | None
    checks: dict[str, Any]
