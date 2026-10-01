"""The `focus-wake` test tick (P2-15, fakes only; stub until implemented)."""

from datetime import datetime
from typing import Any


async def wake(client: Any, now: datetime) -> int:
    """Send `{"kind": "tick", "now": ...}` to every waiting focus workflow."""
    raise NotImplementedError
