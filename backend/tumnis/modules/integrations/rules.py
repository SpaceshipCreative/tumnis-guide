"""integrations pure rules: no I/O, `now` and `tz` passed in.

Taint (SAF-1 groundwork, FR-14.2): outside content is untrusted, and whatever is made from
it carries the taint. Ownership: each canonical record type lives in one module's table.

Connections (P3-02): a connection's status and what a sync outcome makes of it, the
backfill window (Data flow rule 3), the sync cadence with failure backoff, and each
provider's request limit.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

RecordOwner = Literal["integrations", "calendar", "knowledge"]

RECORD_OWNERS: Final[dict[str, RecordOwner]] = {
    "person": "integrations",
    "thread": "integrations",
    "message": "integrations",
    "note": "integrations",
    "artifact": "integrations",
    "event": "calendar",
    "document": "knowledge",
}


def propagate_taint(*tainted: bool) -> bool:
    """Tainted when any input is (SAF-1): a context item, a proposal or a task made from
    outside content carries the taint of what it came from. No inputs: trusted."""
    return any(tainted)


def record_owner(record_type: str) -> RecordOwner:
    """The module that owns a canonical record type's table; ValueError for a type no
    module owns."""
    try:
        return RECORD_OWNERS[record_type]
    except KeyError:
        raise ValueError(f"unknown canonical record type {record_type!r}") from None


# --- Connections (P3-02) -----------------------------------------------------------------------


class ConnectionStatus(StrEnum):
    pending_auth = "pending_auth"  # created, never signed in
    ok = "ok"
    syncing = "syncing"
    degraded = "degraded"  # the last sync failed; retrying with backoff
    auth_required = "auth_required"  # the grant is gone; the user must reconnect
    disabled = "disabled"  # disconnected


class SyncOutcome(StrEnum):
    success = "success"
    transient_error = "transient_error"
    auth_error = "auth_error"


class ConnectionSettings(BaseModel):
    """What the user sets per connection; never credentials."""

    backfill_days: int = Field(default=30, ge=1, le=3650)  # Data flow rule 3
    sync_every_min: int | None = Field(default=None, ge=1, le=1440)  # None: provider default
    allowlist: list[str] = Field(default_factory=list, max_length=500)  # channels, prefixes
    extra: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ProviderLimit:
    requests: int
    period_s: int


# Per provider account (plan defaults; Granola documents about 100 a minute, so 90 leaves
# headroom). `fake` is the framework's scripted source.
PROVIDER_LIMITS: Final[Mapping[str, ProviderLimit]] = {
    "fake": ProviderLimit(requests=10, period_s=60),
    "granola": ProviderLimit(requests=90, period_s=60),
    "inbox_zero": ProviderLimit(requests=60, period_s=60),
    "slack": ProviderLimit(requests=45, period_s=60),
    "google_drive": ProviderLimit(requests=300, period_s=60),
}
DEFAULT_LIMIT: Final = ProviderLimit(requests=60, period_s=60)  # plan default

# Minutes between syncs (PRD where noted, else plan defaults).
DEFAULT_SYNC_MIN: Final[Mapping[str, int]] = {
    "fake": 5,
    "inbox_zero": 5,  # PRD
    "granola": 30,
    "chat": 5,
    "google_drive": 10,  # PRD
    "s3": 15,  # PRD
}
FALLBACK_SYNC_MIN: Final = 15  # plan default for a provider not listed
MAX_BACKOFF_MIN: Final = 60  # plan default

# What a sync outcome makes of each status (T-P3-02-08). A transient error leaves a
# connection that never synced, or that waits for a new sign-in, where it is.
STATUS_TABLE: Final[Mapping[tuple[ConnectionStatus, SyncOutcome], ConnectionStatus]] = {}


def provider_limit(provider: str) -> ProviderLimit:
    raise NotImplementedError


def sync_every(provider: str, settings: ConnectionSettings) -> int:
    raise NotImplementedError


def backfill_start(
    now: datetime, settings: ConnectionSettings, provider_cap_days: int | None
) -> datetime:
    raise NotImplementedError


def next_sync_at(
    now: datetime, last: datetime | None, every_min: int, failures: int, jitter_s: float = 0
) -> datetime:
    raise NotImplementedError


def status_after(prev: ConnectionStatus, outcome: SyncOutcome) -> ConnectionStatus:
    raise NotImplementedError
