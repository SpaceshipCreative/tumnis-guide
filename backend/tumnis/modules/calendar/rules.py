"""calendar pure rules: no I/O, `now` and `tz` passed in.

- `READONLY_SCOPES`: the only Google scopes Tumnis asks for (Data flow rule 1).
- `map_event`: one Google event to the fields of a canonical Event, or a Tombstone for a
  cancelled one (P1-09, FR-14.4).
- `sync_window`: the bounded time window each sync reads (plan default: yesterday through
  today + 14 days, in the workspace timezone).
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime, tzinfo
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel

READONLY_SCOPES: Final = frozenset(
    {
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    }
)
WINDOW_DAYS_AHEAD: Final = 14  # plan default
SYNC_EVERY_MINUTES: Final = 10  # plan default
REFRESH_SKEW_S: Final = 60  # refresh an access token this close to its expiry

AccountStatus = Literal["connected", "needs_reauth"]


class Tombstone(BaseModel):
    """A cancelled event: the row with this external id is soft-deleted."""

    external_id: str


class MappedEvent(BaseModel):
    """The canonical fields of one Google event (the calendar api adds `fetched_at` to
    make it an `EventRecord`)."""

    external_id: str
    calendar_id: str
    provider_url: str | None = None
    title: str | None = None
    start_at: AwareDatetime
    end_at: AwareDatetime
    all_day: bool = False
    attendees: list[str]
    busy: bool


def map_event(
    raw: Mapping[str, Any], *, calendar_id: str, self_email: str, calendar_tz: tzinfo = UTC
) -> MappedEvent | Tombstone:
    """status 'cancelled' -> Tombstone(external_id); otherwise the event's fields.
    busy = transparency != 'transparent' and self attendee responseStatus != 'declined';
    all-day events (start.date) -> busy only if transparency == 'opaque' (plan default),
    and their dates are midnights in `calendar_tz` (the calendar's time zone).
    external_id = f"{calendar_id}:{raw['id']}"; provider_url = raw['htmlLink']."""
    raise NotImplementedError


def sync_window(today: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start of yesterday, end of today + 14 days] in UTC (plan default)."""
    raise NotImplementedError


def needs_refresh(expires_at: datetime, now: datetime) -> bool:
    """True when the access token expires within REFRESH_SKEW_S of `now`."""
    raise NotImplementedError
