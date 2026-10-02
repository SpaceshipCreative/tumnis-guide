"""Close the day shows the unattended queue (P4-04, J7): `GET /v1/day/{day}/summary` (P1-18,
R-37) gains `queued_unattended`, every task queued for tonight in queued order, each with
whether it will run and, when it will not, the refusal it would get now and its plain words.

Day: Monday 2026-03-09 in New York (the `workspace` fixture)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.planning.tests.integration._plan import MONDAY, new_project
from tumnis.modules.planning.tests.integration._unattended import (
    ai_task,
    pause_project,
    queue,
    set_window,
    taint,
)

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("J7")
@pytest.mark.wp("P4-04")
@pytest.mark.xfail(strict=True, reason="spec:P4-04")
async def test_day_close_lists_queued_with_reasons(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock, session_client: SessionClient
) -> None:
    """T-P4-04-10
    Three queued AI tasks: one green, one tainted, one in a paused project. The day close
    lists all three in queued order: the green one will run, the tainted one will not
    ("From outside content: needs you"), the paused project's will not ("Project paused").
    An AI task nobody queued is not listed; `queued_overnight` names the same three.
    """
    acme = await new_project(workspace, clock, "Acme site")
    beta = await new_project(workspace, clock, "Beta app")
    await set_window(workspace, clock)
    green = await ai_task(workspace, clock, acme, "Fix footer link")
    tainted = await ai_task(workspace, clock, acme, "Reply to the client's request")
    taint(db, tainted)
    paused = await ai_task(workspace, clock, beta, "Update the sitemap")
    await ai_task(workspace, clock, acme, "Not queued")
    for task in (green, tainted, paused):
        await queue(workspace, clock, task)
        clock.advance(timedelta(minutes=1))
    await pause_project(workspace, clock, beta)

    answer = await session_client.get(f"/v1/day/{MONDAY.isoformat()}/summary")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    listed = body["queued_unattended"]
    assert [item["task_id"] for item in listed] == [str(green), str(tainted), str(paused)]
    assert [item["title"] for item in listed] == [
        "Fix footer link",
        "Reply to the client's request",
        "Update the sitemap",
    ]
    assert [item["will_run"] for item in listed] == [True, False, False]
    assert [item["refusal"] for item in listed] == [None, "tainted", "paused"]
    assert [item["reason"] for item in listed] == [
        None,
        "From outside content: needs you",
        "Project paused",
    ]
    assert all(item["queued_at"] for item in listed)
    assert [ref["task_id"] for ref in body["queued_overnight"]] == [
        str(green),
        str(tainted),
        str(paused),
    ]
