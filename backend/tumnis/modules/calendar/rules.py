"""calendar pure rules: no I/O, `now` and `tz` passed in.

- `READONLY_SCOPES`: the only Google scopes Tumnis asks for (Data flow rule 1).
- `map_event`: one Google event to the fields of a canonical Event, or a Tombstone for a
  cancelled one (P1-09, FR-14.4).
- `sync_window`: the bounded time window each sync reads (plan default: yesterday through
  today + 14 days, in the workspace timezone).
- `free_blocks`: a working window minus the busy events of every account (P1-10, FR-1.3);
  `Interval` is `tumnis.core.types.Interval`, so the planning rules share it.
"""

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel

from tumnis.core.types import Interval as SharedInterval

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
    external_id = f"{calendar_id}:{raw['id']}"
    if raw.get("status") == "cancelled":
        return Tombstone(external_id=external_id)
    start, end = raw["start"], raw["end"]
    all_day = "date" in start
    transparency = raw.get("transparency")
    declined = _self_response(raw.get("attendees", ()), self_email) == "declined"
    busy = (transparency == "opaque" if all_day else transparency != "transparent") and not declined
    return MappedEvent(
        external_id=external_id,
        calendar_id=calendar_id,
        provider_url=raw.get("htmlLink"),
        title=raw.get("summary"),
        start_at=_instant(start, calendar_tz),
        end_at=_instant(end, calendar_tz),
        all_day=all_day,
        attendees=[a["email"] for a in raw.get("attendees", ()) if a.get("email")],
        busy=busy,
    )


def _self_response(attendees: Any, self_email: str) -> str | None:
    """The calendar owner's response: the attendee Google marks `self`, or the one with the
    owner's address."""
    wanted = self_email.casefold()
    for attendee in attendees:
        if attendee.get("self") or str(attendee.get("email", "")).casefold() == wanted:
            response: str | None = attendee.get("responseStatus")
            return response
    return None


def _instant(when: Mapping[str, Any], tz: tzinfo) -> datetime:
    """A Google start or end in UTC: `dateTime` carries its offset; an all-day `date` is
    midnight in the calendar's zone."""
    if "dateTime" in when:
        return datetime.fromisoformat(when["dateTime"]).astimezone(UTC)
    return datetime.combine(date.fromisoformat(when["date"]), time(0), tz).astimezone(UTC)


def sync_window(today: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start of yesterday, end of today + 14 days] in UTC (plan default)."""
    start = datetime.combine(today - timedelta(days=1), time(0), tz)
    end = datetime.combine(today + timedelta(days=WINDOW_DAYS_AHEAD + 1), time(0), tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def needs_refresh(expires_at: datetime, now: datetime) -> bool:
    """True when the access token expires within REFRESH_SKEW_S of `now`."""
    return expires_at - now <= timedelta(seconds=REFRESH_SKEW_S)


# --- Free blocks (P1-10, FR-1.3) ---------------------------------------------------------------

Interval = SharedInterval  # shared with the planning rules (tumnis.core.types)
MIN_BLOCK_MINUTES: Final = 15  # plan default


def free_blocks(
    window: Interval | None, busy: Iterable[Interval], *, min_minutes: int = MIN_BLOCK_MINUTES
) -> list[Interval]:
    """The window minus the union of the busy intervals, clipped to the window; gaps
    shorter than `min_minutes` are dropped. The result is sorted, pairwise disjoint and
    never touching (adjacent gaps are merged). No window (a day off) has no blocks."""
    if window is None:
        return []
    return [gap for gap in subtract(window, busy) if gap.minutes >= min_minutes]


def clip(interval: Interval, window: Interval) -> Interval | None:
    """The part of `interval` inside `window`, or None when they do not overlap."""
    start, end = max(interval.start, window.start), min(interval.end, window.end)
    return Interval(start, end) if start < end else None


def subtract(window: Interval, busy: Iterable[Interval]) -> list[Interval]:
    """The gaps of `window` no busy interval covers, by one sweep over the busy intervals
    in start order: each gap ends where the next busy time starts, and busy intervals that
    overlap or touch merge into one, so two gaps never touch."""
    gaps: list[Interval] = []
    cursor = window.start
    for event in sorted(busy):
        if event.start >= window.end:
            break
        if event.start > cursor:
            gaps.append(Interval(cursor, event.start))
        cursor = max(cursor, event.end)
    if cursor < window.end:
        gaps.append(Interval(cursor, window.end))
    return gaps
