"""notifications event payload models (P2-16, A8). They live apart from `events.py` so
`api.py` can emit them while `events.py` calls `api.py` (the tasks module's pattern).

- `notification.ready`: a notification goes out now (`rules.channels_now` names Discord):
  a review item or focus event decided "now", or the batch Quiet held, released at the next
  break. Emitted once, in the transaction that writes the row. Its subscriber
  `notifications.deliver_notification` delivers it to Discord through the master; its
  dead letter (REL-3) is what Settings retries.
"""

from typing import ClassVar, Literal
from uuid import UUID

from tumnis.core.events import EventPayload, event_type

__all__ = ["NotificationReadyV1"]


@event_type("notification.ready", 1)
class NotificationReadyV1(EventPayload):
    event_name: ClassVar[str] = "notification.ready"
    schema_version: Literal[1] = 1
    notification_id: UUID
    kind: str  # a review kind, "focus.<event kind>" or "batch"
