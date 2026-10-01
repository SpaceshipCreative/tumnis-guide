"""The local metrics summary (P1-18, PRD Success metrics: "Measure locally; nothing
leaves the server"): `GET /v1/metrics/summary?from=&to=` answers from Postgres alone."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.usage.tests.integration import _usage

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("Success metrics")
@pytest.mark.wp("P1-18")
async def test_summary_makes_no_outbound_calls(
    session_client: SessionClient, clock: FixedClock
) -> None:
    """T-P1-18-07
    With sockets blocked for everything but Postgres, the summary endpoint answers.
    """
    # Something to measure: one open today.
    assert (await session_client.post("/v1/metrics/open", json={})).status_code == 204
    clock.set(datetime(2026, 3, 9, 21, tzinfo=UTC))

    with _usage.outbound_blocked():
        summary = await session_client.get(
            "/v1/metrics/summary", params={"from": "2026-03-02", "to": "2026-03-09"}
        )

    assert summary.status_code == 200, summary.text
    body = summary.json()
    metrics = {m["key"]: m for m in body["metrics"]}
    assert {
        "daily_open_rate",
        "tasks_completed_per_working_day",
        "rollover_rate",
        "estimate_error",
    } <= set(metrics)
    # Each metric carries its PRD target; no data yet is null, never 0.
    assert all(m["target"] for m in metrics.values())
    assert metrics["daily_open_rate"]["value"] == pytest.approx(1 / 8)
    assert metrics["rollover_rate"]["value"] is None
    assert metrics["estimate_error"]["value"] is None
    assert body["plan_days_in_a_row"] == 0
