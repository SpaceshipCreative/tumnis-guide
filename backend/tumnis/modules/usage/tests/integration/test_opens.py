"""App opens (P1-18, PRD Success metrics): the PWA calls `POST /v1/metrics/open` once per
app start and after 30 minutes hidden; the server raises the `app_open` counter on the
workspace's local day, so the daily open rate counts open days, not opens.

Day: Monday 2026-03-09 in New York (the `workspace` fixture), EDT (UTC-4)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.usage.tests.integration import _usage

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

MONDAY = date(2026, 3, 9)


@pytest.mark.req("Success metrics")
@pytest.mark.wp("P1-18")
@pytest.mark.xfail(strict=True, reason="spec:P1-18")
async def test_open_counted_once_per_day(
    db: DbUrls, workspace: WorkspaceHandle, session_client: SessionClient, clock: FixedClock
) -> None:
    """T-P1-18-06
    Several opens on one local day give one open day.
    """
    # 08:05, 14:00 and 23:30 local on Monday; the last is already Tuesday in UTC.
    for at in ("2026-03-09T12:05:00", "2026-03-09T18:00:00", "2026-03-10T03:30:00"):
        clock.set(datetime.fromisoformat(at).replace(tzinfo=UTC))
        opened = await session_client.post("/v1/metrics/open", json={})
        assert opened.status_code == 204, opened.text

    assert _usage.counter_value(db, workspace.id, MONDAY, "app_open") == 3
    assert _usage.counter_value(db, workspace.id, date(2026, 3, 10), "app_open") is None

    summary = await session_client.get(
        "/v1/metrics/summary", params={"from": "2026-03-09", "to": "2026-03-10"}
    )
    assert summary.status_code == 200, summary.text
    metrics = {m["key"]: m for m in summary.json()["metrics"]}
    # One open day (Monday) out of the two days asked for.
    assert metrics["daily_open_rate"]["value"] == pytest.approx(0.5)
