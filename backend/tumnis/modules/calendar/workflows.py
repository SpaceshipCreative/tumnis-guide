"""calendar DBOS workflows and steps (P1-09). Interface stubs until the implementation."""

from typing import Any

from tumnis.core.clock import Clock
from tumnis.modules.calendar.adapters.port import GoogleCalendarPort


def use(
    api: GoogleCalendarPort | None = None, clock: Clock | None = None
) -> tuple[GoogleCalendarPort | None, Clock | None]:
    """Swap the Google API and the clock the workflows use (tests); returns the previous."""
    raise NotImplementedError


async def connector_sync(workspace_id: str, connection_id: str) -> dict[str, Any]:
    raise NotImplementedError
