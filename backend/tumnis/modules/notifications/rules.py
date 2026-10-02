"""notifications pure rules: no I/O, `now` and `tz` passed in.

Delivery (P2-16's interface, built here as the smallest seam P4-05 needs; P2-16 adds the
Discord channel on top of the same rules, so level and batching are never duplicated):
- `delivery_decision(level, any_task_in_progress, kind)`: "now" or "batch";
- `flush_due(batch, any_task_in_progress, now, day_end_at)`: the next natural break;
- `channels_now(decision)`: the channels a notification reaches at once (P2-16): in-app
  always (the badge and the focus bar, FR-8.1), Discord through the master and browser
  push only when the decision is "now".

Browser push (P4-05, FR-8.3):
- `deep_link_for(item)`: `/review?kind=<kind>&item=<id>`, the review route's own params;
- `push_payload(source)`: the minimal text a push carries (Data flow rule 6);
- `batch_payload(count)`: the one push a flushed batch sends;
- `endpoint_allowed(endpoint)`: https to a known browser push service only (SEC-5).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import AnyUrl, BaseModel, ValidationError

Level = Literal["quiet", "nudge", "coach", "guardrail"]
Decision = Literal["now", "batch"]
Channel = Literal["in_app", "push", "discord"]
NotificationKind = str  # a registered review kind (R-05), or "focus.<event kind>"
FOCUS_PREFIX: Final = "focus."

# Push services every current browser subscribes through (plan default allow-list).
PUSH_HOSTS: Final = ("fcm.googleapis.com", "updates.push.services.mozilla.com")
PUSH_HOST_SUFFIXES: Final = (".push.apple.com", ".notify.windows.com")
MAX_PUSH_BYTES: Final = 4096  # the encrypted body every push service must accept (RFC 8030)
ECE_OVERHEAD: Final = 103  # aes128gcm: 86-byte header, 1 padding delimiter, 16-byte tag
PUSH_TTL_S: Final = 24 * 3600  # how long a push service holds a push for an offline browser


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
    return plaintext_bytes + ECE_OVERHEAD


def delivery_decision(level: Level, any_task_in_progress: bool, kind: NotificationKind) -> Decision:
    """Quiet and a task In progress: batch everything (FR-8.4, UX 6). Otherwise focus events
    at levels that fire them go now; review-type items go now unless Quiet with a task In
    progress. The in-app review badge always updates at once (FR-8.1)."""
    del kind  # every kind follows the same table; focus only emits the events its level fires
    return "batch" if level == "quiet" and any_task_in_progress else "now"


def flush_due(
    batch: Sequence[NotificationView],
    any_task_in_progress: bool,
    now: datetime,
    day_end_at: datetime,
) -> bool:
    """Next natural break: no task In progress, or the day's working hours ended."""
    return bool(batch) and (not any_task_in_progress or now >= day_end_at)


def channels_now(decision: Decision) -> tuple[Channel, ...]:
    """The channels a notification reaches at once: the in-app review badge and focus bar
    whatever was decided (FR-8.1: they never wait), Discord (the master's notify run) and
    browser push only when the decision is "now"; a batched one reaches them with its
    batch, at the next natural break."""
    return ("in_app", "discord", "push") if decision == "now" else ("in_app",)


def deep_link_for(item: ReviewItemLite) -> str:
    """The review route's own params (R-05): `/review?kind=<kind>&item=<id>`; a kind that is
    not a registry slug is left out, so the link still opens the item."""
    if _KIND.fullmatch(item.kind):
        return f"/review?kind={item.kind}&item={item.id}"
    return f"/review?item={item.id}"


def push_payload(source: ReviewItemLite | FocusEventLite) -> PushPayload:
    """The kind and a short label, never the item's content (Data flow rule 6): a review
    item names its kind and project (email-like words dropped); a focus event names its
    kind only, since its message names the task. Under the 4 KB push bound encrypted."""
    if isinstance(source, FocusEventLite):
        return PushPayload(
            kind=f"{FOCUS_PREFIX}{source.kind}",
            title=_FOCUS_TITLES.get(source.kind, "Focus"),
            body="Open Tumnis to see where you are.",
            url="/",
            tag=str(source.id),
        )
    title = f"Waiting on you: {_words(source.kind)}"
    project = _label(source.project_name)
    if project:
        title = f"{title} in {project}"
    return PushPayload(
        kind=source.kind,
        title=title,
        body="Open Tumnis to review it.",
        url=deep_link_for(source),
        tag=str(source.id),
    )


def batch_payload(count: int) -> PushPayload:
    """The one push a flushed batch sends: how many items waited, linking to the queue."""
    items = "1 item" if count == 1 else f"{count} items"
    return PushPayload(
        kind="batch",
        title=f"Waiting on you: {items}",
        body="Held while you were busy. Open Tumnis to review.",
        url="/review",
        tag="batch",
    )


def endpoint_allowed(endpoint: str) -> bool:
    """https only and host in the push-service allow-list (plan default: fcm.googleapis.com,
    updates.push.services.mozilla.com, *.push.apple.com, *.notify.windows.com)."""
    try:
        url = AnyUrl(endpoint)
    except ValidationError:
        return False
    host = url.host or ""
    if url.scheme != "https" or url.username or url.password or url.port != HTTPS_PORT:
        return False
    if host in PUSH_HOSTS:
        return True
    return any(
        host.endswith(suffix) and len(host) > len(suffix) and _LABEL.fullmatch(host[: -len(suffix)])
        for suffix in PUSH_HOST_SUFFIXES
    )


# --- helpers -------------------------------------------------------------------------------

HTTPS_PORT: Final = 443
LABEL_MAX: Final = 60  # a project name in a push title, at most
_KIND: Final = re.compile(r"[a-z][a-z0-9_]{2,40}")
_LABEL: Final = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")
_FOCUS_TITLES: Final = {
    "block_start": "Focus: time to start",
    "not_started": "Focus: not started yet",
    "check_in_due": "Focus: check-in",
    "switched": "Focus: switched tasks",
    "stuck": "Focus: stuck?",
    "block_end": "Focus: block ending",
    "day_end": "Focus: end of the day",
}


def _words(slug: str) -> str:
    return slug.replace("_", " ")


def _label(name: str | None) -> str | None:
    """A project name fit for a push title: words holding '@' (email-like) dropped, control
    characters gone, at most LABEL_MAX characters."""
    if not name:
        return None
    words = [w for w in name.split() if "@" not in w]
    text = "".join(ch for ch in " ".join(words) if ch.isprintable())
    return text[:LABEL_MAX].strip() or None
