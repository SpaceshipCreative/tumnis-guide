"""A trashed task's block is not planned time (P1-12, FR-2.6; PR #77 review): trashing
only sets the task's `deleted_at` (and a purge deletes the row), while its `plan_items`
row stays, since `plan_items.task_id` has no foreign key. The week leaves the block out,
and another task can be scheduled into that time."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.planning.tests.integration._week import local, schedule, seed_client

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

MONDAY = date(2026, 3, 9)  # the seed's calendar day; 14:00 to 16:00 local is free


@pytest.mark.req("FR-2.6")
@pytest.mark.wp("P1-12")
async def test_trashed_task_block_frees_its_time(
    app: FastAPI, seed: SeedResult, clock: FixedClock
) -> None:
    client = await seed_client(app, clock)
    project = seed.ids["p_acme"]
    task = seed.ids["t_acme_6a"]  # Human, 45 minutes
    other = seed.ids["t_acme_2"]  # Hybrid, 45 minutes
    start, end = local(MONDAY, "14:00"), local(MONDAY, "14:45")

    first = await schedule(client, MONDAY, task, start, end)
    assert first.status_code == 200, first.text
    version = (await client.get(f"/v1/tasks/{task}")).json()["version"]
    trashed = await client.request("DELETE", f"/v1/tasks/{task}", json={"version": version})
    assert trashed.status_code == 200, trashed.text

    week = await client.get(
        f"/v1/plan/week/{MONDAY.isoformat()}", params={"project_id": str(project)}
    )
    assert week.status_code == 200, week.text
    assert week.json()["days"][0]["planned"] == []

    again = await schedule(client, MONDAY, other, start, end)
    assert again.status_code == 200, again.text
