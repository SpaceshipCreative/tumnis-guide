"""Shared fixtures (A5), loaded for the whole rootdir by backend/conftest.py.

Core and module tests live under tumnis/**/tests (R-16), outside backend/tests, so the
shared fixtures live in this plugin rather than in backend/tests/conftest.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tumnis.core.adapters.registry import AdapterMode, registered, resolve
from tumnis.core.clock import FixedClock

# Monday of the US DST start week: the clocks sprang forward the day before.
CLOCK_START = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Integration tests never forget to open the socket block."""
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.enable_socket)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(CLOCK_START)


class Fakes:
    """Every registered adapter built in one mode, once per test, by name."""

    def __init__(self, mode: AdapterMode = "fake") -> None:
        self.mode = mode
        self._built: dict[str, Any] = {}

    def __getitem__(self, name: str) -> Any:
        if name not in self._built:
            self._built[name] = resolve(name, self.mode)
        return self._built[name]

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in registered())


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Fakes:
    """Handle to every adapter fake for scripting and assertions (fakes["calendar.google"])."""
    import tumnis.wiring  # noqa: F401, PLC0415

    monkeypatch.setenv("TUMNIS_ADAPTERS", "fake")
    return Fakes(mode="fake")
