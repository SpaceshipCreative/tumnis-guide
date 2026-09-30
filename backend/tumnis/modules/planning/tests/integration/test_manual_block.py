"""Scheduling one task by hand (P1-12, FR-2.6): `PATCH /v1/plan/{day}/items/{task_id}` with
`{block_start, block_end, version?}` upserts the task's item in the day's published plan
(a `manual` plan when the day has none). A block that is not free time (a busy event or
another planned block) is refused with 409 `block_not_free` and the current free blocks,
and nothing is written."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.planning.tests.integration._week import (
    iso,
    local,
    plan_rows,
    schedule,
    seed_client,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

MONDAY = date(2026, 3, 9)  # the seed's calendar day (the clock's): New York is UTC-4


@pytest.mark.req("FR-2.6")
@pytest.mark.wp("P1-12")
async def test_busy_block_refused_409(
    app: FastAPI, seed: SeedResult, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-12-06
    The seed day has a busy event 13:00 to 14:00 local. A PATCH of a 45-minute Human task
    into 13:30 to 14:15 answers 409 `block_not_free` with the day's current free blocks and
    writes no `plan_items` row; a PATCH into the free block at 14:00 to 14:45 creates the
    item (in a `manual` plan published for the day); another 45-minute task aimed at 14:30
    to 15:15 overlaps that planned block and is refused the same way, leaving the rows as
    they were.
    """
    from sqlalchemy import text  # noqa: PLC0415

    client = await seed_client(app, clock)
    task = seed.ids["t_acme_6a"]  # Human, 45 minutes, Backlog
    other = seed.ids["t_acme_2"]  # Hybrid, 45 minutes, Today
    calendar = await client.get(f"/v1/plan/{MONDAY.isoformat()}/calendar")
    assert calendar.status_code == 200, calendar.text
    free_blocks = calendar.json()["free_blocks"]
    assert {
        "start": iso(local(MONDAY, "14:00")),
        "end": iso(local(MONDAY, "16:00")),
        "minutes": 120,
    } in free_blocks

    refused = await schedule(client, MONDAY, task, local(MONDAY, "13:30"), local(MONDAY, "14:15"))
    assert refused.status_code == 409, refused.text
    assert refused.headers["content-type"].startswith("application/problem+json")
    problem = refused.json()
    assert problem["code"] == "block_not_free"
    assert problem["current"]["free_blocks"] == free_blocks
    assert await plan_rows(owner_session) == []

    created = await schedule(client, MONDAY, task, local(MONDAY, "14:00"), local(MONDAY, "14:45"))
    assert created.status_code == 200, created.text
    item = created.json()
    assert item["task_id"] == str(task)
    assert item["day"] == MONDAY.isoformat()
    assert item["block_start"] == iso(local(MONDAY, "14:00"))
    assert item["block_end"] == iso(local(MONDAY, "14:45"))
    assert await plan_rows(owner_session) == [
        (task, local(MONDAY, "14:00"), local(MONDAY, "14:45"))
    ]
    plans = (
        await owner_session.execute(text("SELECT day, source, trigger, status FROM daily_plans"))
    ).all()
    assert [tuple(row) for row in plans] == [(MONDAY, "manual", "manual", "published")]

    overlap = await schedule(client, MONDAY, other, local(MONDAY, "14:30"), local(MONDAY, "15:15"))
    assert overlap.status_code == 409, overlap.text
    assert overlap.json()["code"] == "block_not_free"
    assert overlap.json()["current"]["free_blocks"] == free_blocks
    assert await plan_rows(owner_session) == [
        (task, local(MONDAY, "14:00"), local(MONDAY, "14:45"))
    ]
