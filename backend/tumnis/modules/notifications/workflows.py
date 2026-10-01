"""notifications DBOS workflows and steps."""

from collections.abc import Callable
from typing import Any

PushFactory = Callable[[str, Any], Any]


def use(
    factory: PushFactory | None = None, *, retry_delays_s: tuple[float, ...] | None = None
) -> None:
    """Test seam: the push adapter factory and the waits between attempts (P4-05)."""
