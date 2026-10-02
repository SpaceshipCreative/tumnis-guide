"""calendar adapters: one file per outside dependency, each with a fake.

`calendar.google` is the Google Calendar API (`google.py`, replayed by `fake.py`). The
Google Calendar connector registers in `calendar.api`, which builds on this adapter (an
adapter here never imports the api)."""

from tumnis.core.adapters.registry import register_adapter
from tumnis.core.fake_scripts import register_fake_script
from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar, parse_calendar_script
from tumnis.modules.calendar.adapters.google import GoogleCalendarApi
from tumnis.modules.calendar.adapters.port import GoogleCalendarPort

register_adapter(
    "calendar.google", port=GoogleCalendarPort, real=GoogleCalendarApi, fake=FakeGoogleCalendar
)
register_fake_script("calendar.google", parse_calendar_script)  # R-37, A1.3's scenarios
