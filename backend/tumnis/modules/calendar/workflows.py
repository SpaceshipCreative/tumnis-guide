"""calendar DBOS workflows and steps (P1-09). Interface stubs until the implementation."""

from typing import Any

from tumnis.core.clock import Clock
from tumnis.modules.calendar.adapters.port import GoogleCalendarPort


_api: GoogleCalendarPort | None = None  # None: resolved from the registry on first use
_clock: Clock | None = None  # None: the system clock


def use(
    api: GoogleCalendarPort | None = None, clock: Clock | None = None
) -> tuple[GoogleCalendarPort | None, Clock | None]:
    """Swap the Google API and the clock the workflows use (tests); returns the previous."""
    global _api, _clock  # noqa: PLW0603  # the workflows' one seam for tests
    previous = (_api, _clock)
    _api, _clock = api, clock
    return previous


async def connector_sync(workspace_id: str, connection_id: str) -> dict[str, Any]:
    raise NotImplementedError
