"""The project week view (P1-12, FR-2.6): `GET /v1/plan/week/{monday}?project_id=` answers
Monday to Sunday in the workspace timezone, each day with its working window, free blocks,
the events (the project's meetings by title, other busy time as `Busy` without one), the
project's tasks due that day and the blocks planned that day. It is a projection over
`plan_items`: a block scheduled by hand is read back here."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.planning.tests.integration._week import (
    add_meetings,
    iso,
    link_project,
    local,
    schedule,
    seed_client,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fixtures import QueryCounter
    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

MONDAY = date(2026, 3, 9)  # the seed's calendar day (the clock's)
NEXT_MONDAY = MONDAY + timedelta(days=7)


@pytest.mark.req("FR-2.6")
@pytest.mark.wp("P1-12")
async def test_query_count_fixed(
    app: FastAPI, seed: SeedResult, clock: FixedClock, query_counter: QueryCounter
) -> None:
    """T-P1-12-08
    The seed week (six busy events on Monday, none of them the project's) and the next week
    (40 meetings over seven days from two accounts, half of them with an attendee at the
    project's linked domain, and one block scheduled by hand) cost the same number of SQL
    statements. The next week's payload lists seven days from Monday; the project's
    meetings keep their titles and `matched`, other busy events are `Busy` without a title,
    and the planned block names its task.
    """
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    client = await seed_client(app, clock)
    ctx = WorkspaceContext(seed.ids["ws_main"], SYSTEM_ACTOR)
    project = seed.ids["p_acme"]
    await link_project(ctx, project, [("domain", "acme.example.org")])
    meetings = [
        (
            local(NEXT_MONDAY + timedelta(days=n % 7), "09:00") + timedelta(minutes=15 * (n // 7)),
            local(NEXT_MONDAY + timedelta(days=n % 7), "09:10") + timedelta(minutes=15 * (n // 7)),
            ["drew@acme.example.org"] if n % 2 == 0 else ["casey@example.net"],
        )
        for n in range(40)
    ]
    await add_meetings(ctx, "avery@example.com", meetings[::2])
    await add_meetings(ctx, "blake@example.org", meetings[1::2])
    task = seed.ids["t_acme_6a"]  # Human, 45 minutes
    tuesday = NEXT_MONDAY + timedelta(days=1)
    scheduled = await schedule(
        client, tuesday, task, local(tuesday, "14:00"), local(tuesday, "14:45")
    )
    assert scheduled.status_code == 200, scheduled.text

    def week(monday: date) -> str:
        return f"/v1/plan/week/{monday.isoformat()}?project_id={project}"

    warm = await client.get(week(MONDAY))
    assert warm.status_code == 200, warm.text

    query_counter.watch(core_db.app_engine())
    counts: dict[date, int] = {}
    bodies = {}
    for monday in (MONDAY, NEXT_MONDAY):
        query_counter.reset()
        answered = await client.get(week(monday))
        assert answered.status_code == 200, answered.text
        counts[monday] = query_counter.count
        bodies[monday] = answered.json()
    assert counts[MONDAY] == counts[NEXT_MONDAY], query_counter.statements

    body = bodies[NEXT_MONDAY]
    days = body["days"]
    assert [d["day"] for d in days] == [
        (NEXT_MONDAY + timedelta(days=n)).isoformat() for n in range(7)
    ]
    assert {"day", "window", "free_blocks", "events", "due", "planned"} <= set(days[0])
    assert sum(len(d["events"]) for d in days) == 40
    events = [e for d in days for e in d["events"]]
    assert {(e["matched"], e["title"] is None) for e in events} == {(True, False), (False, True)}
    assert all(e["busy"] for e in events)
    assert sum(e["matched"] for e in events) == 20
    [planned] = days[1]["planned"]
    assert planned["task_id"] == str(task)
    assert (planned["start"], planned["end"]) == (
        iso(local(tuesday, "14:00")),
        iso(local(tuesday, "14:45")),
    )

    seed_week = bodies[MONDAY]["days"]
    assert [e["title"] for e in seed_week[0]["events"]] == [None] * 6  # none of them Acme's
    assert all(not e["matched"] for e in seed_week[0]["events"])
