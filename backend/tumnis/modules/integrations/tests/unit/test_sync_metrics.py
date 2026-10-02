"""The connector gauges cover only the framework's providers (P3-02, OBS): calendar's and
the seed's connections sync through their own modules and never set `last_success_at`, so
counting them would report a stale sync that is not one."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from prometheus_client import CollectorRegistry

from tumnis.core.metrics import CONNECTOR_ITEMS, CONNECTOR_SYNC_AGE
from tumnis.modules.integrations import adapters as _adapters  # noqa: F401  (registers `fake`)
from tumnis.modules.integrations.api import export_metrics


class _Conn:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, _stmt: object) -> Any:
        return SimpleNamespace(all=lambda: self._rows)


def _values(gauge: Any) -> dict[str, float]:
    registry = CollectorRegistry()
    registry.register(gauge)
    try:
        return {
            sample.labels["provider"]: sample.value
            for family in registry.collect()
            for sample in family.samples
        }
    finally:
        registry.unregister(gauge)


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
async def test_gauges_skip_connections_the_framework_does_not_sync() -> None:
    rows = [
        SimpleNamespace(provider="fake", age_seconds=120.0, items=7),
        SimpleNamespace(provider="google_calendar", age_seconds=999_999.0, items=0),
        SimpleNamespace(provider="seed", age_seconds=999_999.0, items=0),
    ]
    await export_metrics(_Conn(rows))  # type: ignore[arg-type]
    assert _values(CONNECTOR_SYNC_AGE) == {"fake": 120.0}
    assert _values(CONNECTOR_ITEMS) == {"fake": 7.0}
