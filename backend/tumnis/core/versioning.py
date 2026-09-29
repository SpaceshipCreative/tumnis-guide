"""Optimistic concurrency: a write names the version it read (P0-08 for settings; P0-10
owns the helpers and the 409 handler, REL-2)."""

from collections.abc import Mapping
from typing import Any


class StaleVersion(Exception):  # noqa: N818  # the plan's name (A13, P0-10)
    """The row moved on since the caller read it; `current` is what it holds now."""

    def __init__(self, current: Mapping[str, Any]) -> None:
        super().__init__("stale version")
        self.current = current
