"""notifications pure rules: no I/O, `now` and `tz` passed in.

Delivery (P2-16's interface, built here as the smallest seam P4-05 needs; P2-16 adds the
Discord channel on top of the same rules, so level and batching are never duplicated):
- `delivery_decision(level, any_task_in_progress, kind)`: "now" or "batch";
- `flush_due(batch, any_task_in_progress, now, day_end_at)`: the next natural break.

Browser push (P4-05, FR-8.3):
- `deep_link_for(item)`: `/review?kind=<kind>&item=<id>`, the review route's own params;
- `push_payload(source)`: the minimal text a push carries (Data flow rule 6);
- `batch_payload(count)`: the one push a flushed batch sends;
- `endpoint_allowed(endpoint)`: https to a known browser push service only (SEC-5).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel

Level = Literal["quiet", "nudge", "coach", "guardrail"]
Decision = Literal["now", "batch"]
NotificationKind = str  # a registered review kind (R-05), or "focus.<event kind>"
FOCUS_PREFIX: Final = "focus."

# Push services every current browser subscribes through (plan default allow-list).
PUSH_HOSTS: Final = ("fcm.googleapis.com", "updates.push.services.mozilla.com")
PUSH_HOST_SUFFIXES: Final = (".push.apple.com", ".notify.windows.com")
MAX_PUSH_BYTES: Final = 4096  # the encrypted body every push service must accept (RFC 8030)
ECE_OVERHEAD: Final = 103  # aes128gcm: 86-byte header, 1 padding delimiter, 16-byte tag


class PushPayload(BaseModel):
    """Minimal text: push services are third parties (Data flow rule 6)."""

    schema_version: Literal[1] = 1
    kind: str
    title: str
    body: str
    url: str
    tag: str


class ReviewItemLite(BaseModel):
    """What a push knows of a review item. `body` is the item's content: it never leaves."""

    id: UUID
    kind: str
    project_name: str | None = None
    body: str | None = None


class FocusEventLite(BaseModel):
    """What a push knows of a focus event. `message` names the task: it never leaves."""

    id: UUID
    kind: str
    message: str | None = None


@dataclass(frozen=True)
class NotificationView:
    id: UUID
    kind: NotificationKind
    created_at: datetime


def encrypted_size(plaintext_bytes: int) -> int:
    """The aes128gcm body size of a one-record push (RFC 8188, RFC 8291)."""
    raise NotImplementedError


def delivery_decision(level: Level, any_task_in_progress: bool, kind: NotificationKind) -> Decision:
    """Quiet and a task In progress: batch everything (FR-8.4, UX 6). Otherwise focus events
    at levels that fire them go now; review-type items go now unless Quiet with a task In
    progress. The in-app review badge always updates at once (FR-8.1)."""
    raise NotImplementedError


def flush_due(
    batch: Sequence[NotificationView],
    any_task_in_progress: bool,
    now: datetime,
    day_end_at: datetime,
) -> bool:
    """Next natural break: no task In progress, or the day's working hours ended."""
    raise NotImplementedError


def deep_link_for(item: ReviewItemLite) -> str:
    raise NotImplementedError


def push_payload(source: ReviewItemLite | FocusEventLite) -> PushPayload:
    raise NotImplementedError


def batch_payload(count: int) -> PushPayload:
    raise NotImplementedError


def endpoint_allowed(endpoint: str) -> bool:
    """https only and host in the push-service allow-list (plan default: fcm.googleapis.com,
    updates.push.services.mozilla.com, *.push.apple.com, *.notify.windows.com)."""
    raise NotImplementedError
