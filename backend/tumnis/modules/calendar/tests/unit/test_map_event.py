"""Google events map to canonical Event records by the pure rule (P1-09, FR-14.4).

One case per recording in `tests/recordings/google_calendar/` (two accounts, recurring
instances, all-day, declined, transparent, cancelled): `raw.payload` holds the event as
Google sent it with the calendar it came from; `expected` is the EventRecord (none for a
cancelled event) and `tombstone` the external id a cancelled event removes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "google_calendar"
CASES = sorted(path.stem for path in RECORDINGS.glob("*.json")) or ["no-recordings"]


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
@pytest.mark.parametrize("recording", CASES)
def test_recordings_map_to_expected_events(recording: str) -> None:
    """T-P1-09-02
    map_event over each recording gives its expected record: busy unless transparent or
    declined, all-day busy only when explicitly opaque, external id `<calendar>:<event>`,
    provider URL from htmlLink; a cancelled event gives a Tombstone of its external id.
    """
    from tumnis.modules.calendar.api import EventRecord  # noqa: PLC0415
    from tumnis.modules.calendar.rules import Tombstone, map_event  # noqa: PLC0415

    data: dict[str, Any] = json.loads((RECORDINGS / f"{recording}.json").read_text())
    raw = data["raw"]
    payload = raw["payload"]
    result = map_event(
        payload["event"],
        calendar_id=payload["calendar_id"],
        self_email=payload["self_email"],
        calendar_tz=ZoneInfo(payload["time_zone"]),
    )
    if isinstance(result, Tombstone):
        assert data["expected"] == []
        assert result.external_id == data["tombstone"]
        assert result.external_id == f"{payload['calendar_id']}:{payload['event']['id']}"
        return
    record = EventRecord(**result.model_dump(), fetched_at=raw["fetched_at"])
    assert [record.model_dump(mode="json")] == data["expected"]
    assert "tombstone" not in data
