"""Imported by the kill test's worker subprocesses (`run_worker --import`): every
events.list request the fake Google API serves is appended to the file named by
`CALENDAR_FAKE_PAGE_LOG`, one line per request (its pageToken, `first` for none), so the
test sees across both processes which pages were fetched (T-P1-09-09)."""

import os
from typing import Any

import tumnis.modules.calendar.workflows  # noqa: F401  # registers the calendar workflows
from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar

_served = FakeGoogleCalendar.list_events


async def _logged(self: FakeGoogleCalendar, *args: Any, **kwargs: Any) -> dict[str, Any]:
    token = kwargs.get("page_token")
    with open(os.environ["CALENDAR_FAKE_PAGE_LOG"], "a") as log:  # noqa: ASYNC230
        log.write(f"{token or 'first'}\n")
    return await _served(self, *args, **kwargs)


FakeGoogleCalendar.list_events = _logged  # type: ignore[method-assign]
