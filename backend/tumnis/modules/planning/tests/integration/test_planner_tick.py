"""The morning plan tick (P1-11, FR-4.3): `planner_tick` runs every 5 minutes in UTC and
enqueues one `build_plan` per workspace and local day, in each workspace's own zone."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.planning.tests.integration._plan import (
    MONDAY,
    build_runs,
    builds_settled,
    rows,
    tick,
    until,
)

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
@pytest.mark.xfail(strict=True, reason="spec:P1-11")
async def test_tick_enqueues_once_per_workspace(
    dbos: Any, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """T-P1-11-08
    Two workspaces, New York and Sydney, both past their 08:30 plan time on Monday
    2026-03-09 local at 12:30Z; ticks run twice in the same 5-minute window and twice after:
    one `build_plan` per workspace and day, one published plan each.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415

    sydney = make_workspace(db, "Down under", "Australia/Sydney")

    for at in ("12:30", "12:32"):
        await tick(datetime.fromisoformat(f"2026-03-09T{at}:00+00:00"))
    assert await until(builds_settled)
    for at in ("12:35", "12:40"):
        await tick(datetime.fromisoformat(f"2026-03-09T{at}:00+00:00"))
    assert await until(builds_settled)

    assert len(build_runs()) == 2
    found = rows(
        db,
        "SELECT workspace_id, day, count(*) AS n FROM daily_plans "
        "GROUP BY workspace_id, day ORDER BY workspace_id",
    )
    assert sorted((r["workspace_id"], r["day"], r["n"]) for r in found) == sorted(
        [(workspace.id, MONDAY, 1), (sydney, MONDAY, 1)]
    )
    assert {r["trigger"] for r in rows(db, "SELECT trigger FROM daily_plans")} == {"morning"}
