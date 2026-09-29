"""calendar event payload models (P1-09); `events.py` re-exports them. They live apart so
`api.py` can emit them without importing `events.py`.

- `calendar.synced`: one completed sync of a Google account (its connection) over
  `window` (`start`, `end`, UTC). A failed sync emits none (FR-1.3); the planner and the
  calendar strip re-read `events` on it.
"""

from typing import ClassVar, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from tumnis.core.events import EventPayload, event_type


class SyncWindow(BaseModel):
    start: AwareDatetime
    end: AwareDatetime


@event_type("calendar.synced", 1)
class CalendarSyncedV1(EventPayload):
    event_name: ClassVar[str] = "calendar.synced"
    schema_version: Literal[1] = 1
    connection_id: UUID
    window: SyncWindow
