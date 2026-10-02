"""integrations pure rules: no I/O, `now` and `tz` passed in.

Taint (SAF-1 groundwork, FR-14.2): outside content is untrusted, and whatever is made from
it carries the taint. Ownership: each canonical record type lives in one module's table.

Connections (P3-02): a connection's status and what a sync outcome makes of it, the
backfill window (Data flow rule 3), the sync cadence with failure backoff, and each
provider's request limit.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, model_validator

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
_S = ConnectionStatus
_O = SyncOutcome
STATUS_TABLE: Final[Mapping[tuple[ConnectionStatus, SyncOutcome], ConnectionStatus]] = {
    **{(status, _O.success): (_S.disabled if status is _S.disabled else _S.ok) for status in _S},
    **{
        (status, _O.auth_error): (_S.disabled if status is _S.disabled else _S.auth_required)
        for status in _S
    },
    **{
        (status, _O.transient_error): (
            _S.degraded if status in (_S.ok, _S.syncing, _S.degraded) else status
        )
        for status in _S
    },
}


def provider_limit(provider: str) -> ProviderLimit:
    """The provider's request limit per account (plan default for one not listed)."""
    return PROVIDER_LIMITS.get(provider, DEFAULT_LIMIT)


def sync_every(provider: str, settings: ConnectionSettings) -> int:
    """Minutes between syncs: the connection's setting, else the provider's default."""
    return settings.sync_every_min or DEFAULT_SYNC_MIN.get(provider, FALLBACK_SYNC_MIN)


def backfill_start(
    now: datetime, settings: ConnectionSettings, provider_cap_days: int | None
) -> datetime:
    """How far back the first sync reaches (Data flow rule 3): the connection's backfill
    window, never past what the provider keeps."""
    days = settings.backfill_days
    if provider_cap_days is not None:
        days = min(days, provider_cap_days)
    return now - timedelta(days=days)


def next_sync_at(
    now: datetime, last: datetime | None, every_min: int, failures: int, jitter_s: float = 0
) -> datetime:
    """When the next sync is due. After a success, one interval after the last sync (or
    now); after `failures` failures in a row, an exponential backoff from now, capped at
    MAX_BACKOFF_MIN, plus jitter."""
    if failures <= 0:
        return (last or now) + timedelta(minutes=every_min)
    backoff = min(MAX_BACKOFF_MIN, every_min * 2 ** min(failures, 16))
    return now + timedelta(minutes=backoff, seconds=jitter_s)


def status_after(prev: ConnectionStatus, outcome: SyncOutcome) -> ConnectionStatus:
    """What a sync outcome makes of a connection's status (T-P3-02-08)."""
    return STATUS_TABLE[(prev, outcome)]


# --- Retention (P3-09, SAAS-2) ----------------------------------------------------------------

RETENTION_MIN_DAYS: Final = 7  # plan default
RETENTION_MAX_DAYS: Final = 36_500  # a hundred years: keeps the cutoff a valid datetime


class RetentionSetting(BaseModel):
    """The workspace's retention of ingested email, chat and notes (Settings > Retention,
    section `integrations.retention`). SAAS-2's default keeps content until its project
    is purged; `days` is opt-in and needs a number of days, 7 at least."""

    mode: Literal["keep_until_project_purged", "days"] = "keep_until_project_purged"
    days: int | None = Field(default=None, ge=RETENTION_MIN_DAYS, le=RETENTION_MAX_DAYS)

    @model_validator(mode="after")
    def _days_with_days_mode(self) -> "RetentionSetting":
        if self.mode == "days" and self.days is None:
            raise ValueError("the days mode needs a number of days")
        return self


@dataclass(frozen=True)
class IngestedLite:
    """What retention needs of one ingested record: its time (a message's send time, a
    note's meeting time, a thread's last message, else when it was fetched) and whether an
    open task links it."""

    at: datetime
    linked_to_open_task: bool = False


def purge_cutoff(now: datetime, s: RetentionSetting) -> datetime | None:
    """The instant before which ingested content goes; None keeps everything (the
    default, and a days mode without its number of days)."""
    if s.mode != "days" or s.days is None:
        return None
    return now - timedelta(days=s.days)


def purge_candidates(item: IngestedLite, cutoff: datetime | None, project_archived: bool) -> bool:
    """True if older than cutoff, not archived, not linked to an open task (plan default:
    keep while a task is open)."""
    if cutoff is None or project_archived or item.linked_to_open_task:
        return False
    return item.at < cutoff
