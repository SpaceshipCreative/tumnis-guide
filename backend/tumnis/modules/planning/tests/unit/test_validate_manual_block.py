"""One block scheduled by hand (P1-12, FR-2.6): `validate_manual_block` answers the
validate_plan codes that apply to a single item. Context: Monday 2026-03-09 in New York,
`now` 08:30 local, free blocks 10:00 to 12:00 and 13:00 to 15:00, a block already planned
14:00 to 14:30."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from tumnis.core.types import Interval
from tumnis.modules.planning.rules import Label, PlanTask, validate_manual_block

NEW_YORK = ZoneInfo("America/New_York")
DAY = date(2026, 3, 9)
TASK = UUID("0199aa00-0000-7000-8000-0000000000a1")
PROJECT = UUID("0199aa00-0000-7000-8000-000000000001")


def at(hhmm: str) -> datetime:
    return datetime.combine(DAY, time.fromisoformat(hhmm), NEW_YORK).astimezone(UTC)


def span(start: str, end: str) -> Interval:
    return Interval(at(start), at(end))


FREE = [span("10:00", "12:00"), span("13:00", "15:00")]
PLANNED = [span("14:00", "14:30")]
NOW = at("08:30")


def task(label: Label = "human", estimate: int | None = 45) -> PlanTask:
    return PlanTask(
        task_id=TASK,
        project_id=PROJECT,
        label=label,
        estimate_minutes=estimate,
        status="backlog",
        blocked=False,
        due_on=None,
        priority=1,
        rollover_count=0,
        created_at=at("08:00"),
        eligible=True,
    )


ROWS: list[Any] = [
    pytest.param(task(), span("10:15", "11:00"), NOW, [], id="valid"),
    pytest.param(task("hybrid", 30), span("13:00", "13:30"), NOW, [], id="hybrid_valid"),
    pytest.param(task(), span("10:00", "10:45"), NOW, [], id="starts_with_free_block"),
    pytest.param(task(), span("11:15", "12:00"), NOW, [], id="ends_with_free_block"),
    pytest.param(
        task(), span("11:30", "12:15"), NOW, ["block_outside_free_time"], id="straddles_busy"
    ),
    pytest.param(
        task(), span("09:15", "10:00"), NOW, ["block_outside_free_time"], id="before_window"
    ),
    pytest.param(task(), span("13:45", "14:30"), NOW, ["blocks_overlap"], id="overlaps_planned"),
    pytest.param(task(), span("10:00", "11:00"), NOW, ["block_length_mismatch"], id="wrong_len"),
    pytest.param(
        task(estimate=None), span("10:00", "10:45"), NOW, ["missing_estimate"], id="no_est"
    ),
    pytest.param(task("ai", None), span("10:00", "10:45"), NOW, ["ai_task_has_block"], id="ai"),
    pytest.param(task(), span("10:15", "11:00"), at("10:20"), ["block_in_past"], id="in_past"),
    pytest.param(
        task(),
        span("13:45", "14:45"),
        at("14:00"),
        ["block_length_mismatch", "block_in_past", "blocks_overlap"],
        id="several",
    ),
]


@pytest.mark.req("FR-2.6")
@pytest.mark.wp("P1-12")
@pytest.mark.parametrize(("item", "block", "now", "codes"), ROWS)
def test_validate_manual_block_table(
    item: PlanTask, block: Interval, now: datetime, codes: list[str]
) -> None:
    """Each row's violation codes, in validate_plan's order."""
    found = validate_manual_block(item, block, FREE, PLANNED, now)
    assert [v.code for v in found] == codes
    assert all(v.task_id == TASK for v in found)
