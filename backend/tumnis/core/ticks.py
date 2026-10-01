"""Test ticks (R-37, fakes only): named stand-ins for a schedule or a timer that a test fires
on demand through `POST /v1/test/tick/{schedule_name}` (tumnis.core.testing_routes).

A module registers its tick at import (`register_tick("focus-wake", wake)`, P2-15); the
route calls it with the app's DBOS client and the server clock's time. Nothing outside
the test routes calls a tick, so a deployment without fakes never runs one.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

Tick = Callable[[Any, datetime], Awaitable[int]]  # (DBOS client, now) -> workflows woken

_ticks: dict[str, Tick] = {}


def register_tick(name: str, tick: Tick) -> None:
    """Registers `tick` under `name` (the same one again replaces it)."""
    _ticks[name] = tick


def tick(name: str) -> Tick | None:
    return _ticks.get(name)
