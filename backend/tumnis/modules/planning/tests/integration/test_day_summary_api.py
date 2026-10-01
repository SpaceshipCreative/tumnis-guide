"""Close the day over HTTP (P1-18, J7): `GET /v1/day/{day}/summary` reads the day's tasks,
results and agent runs through tasks' and agents' apis and sorts them with `day_summary`;
`GET /v1/metrics/summary` reads the plans and their decisions for the exit gate. Beyond the
spec table: the rules are proven in tests/unit; these prove the reads behind them.

Day: Monday 2026-03-09 in New York (the `workspace` fixture), EDT (UTC-4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.planning.tests.integration._plan import (
    MONDAY,
    execute,
    move_task,
    new_project,
    new_task,
)

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _run(db: DbUrls, workspace: WorkspaceHandle, kind: str, status: str, at: str) -> None:
    execute(
        db,
        "INSERT INTO runs (workspace_id, profile_id, kind, status, correlation_id, created_by,"
        " started_at, finished_at) VALUES (%s, %s, %s, %s, %s, 'system', %s, %s)",
        workspace.id,
        uuid.uuid4(),
        kind,
        status,
        f"run:{uuid.uuid4()}",
        _at(at),
        _at(at),
    )


@pytest.mark.req("J7")
@pytest.mark.wp("P1-18")
async def test_day_summary_reads_tasks_results_and_runs(
    db: DbUrls, workspace: WorkspaceHandle, session_client: SessionClient, clock: FixedClock
) -> None:
    """Done today (local), results posted today and succeeded enrichment runs today; what is
    left in Today rolls over; Sunday's and Tuesday's work stays out."""
    project = await new_project(workspace, clock, "Acme site")
    shipped = await new_task(
        workspace, clock, project, "Write Acme proposal", label="human", estimate_minutes=30
    )
    late = await new_task(
        workspace, clock, project, "Late finish", label="human", estimate_minutes=30
    )
    sunday = await new_task(
        workspace, clock, project, "Sort receipts", label="human", estimate_minutes=30
    )
    rolling = await new_task(
        workspace, clock, project, "Book the venue", label="human", estimate_minutes=30
    )
    reviewed = await new_task(workspace, clock, project, "Draft the FAQ", label="ai")

    clock.set(_at("2026-03-09T03:30:00"))  # Sunday 23:30 local
    await move_task(workspace, clock, sunday.id, "in_progress", "done")
    clock.set(_at("2026-03-09T15:00:00"))
    await move_task(workspace, clock, shipped.id, "in_progress", "done")
    await move_task(workspace, clock, rolling.id, "today")
    clock.set(_at("2026-03-10T03:30:00"))  # Monday 23:30 local, Tuesday in UTC
    await move_task(workspace, clock, late.id, "in_progress", "done")
    execute(
        db,
        "INSERT INTO results (workspace_id, task_id, run_id, outcome, summary, created_by,"
        " created_at) VALUES (%s, %s, %s, 'done', 'The FAQ draft', 'system', %s)",
        workspace.id,
        reviewed.id,
        uuid.uuid4(),
        _at("2026-03-09T18:00:00"),
    )
    _run(db, workspace, "enrich", "succeeded", "2026-03-09T14:00:00")
    _run(db, workspace, "enrich", "failed", "2026-03-09T14:05:00")
    _run(db, workspace, "enrich", "succeeded", "2026-03-10T05:00:00")  # Tuesday, local

    answer = await session_client.get(f"/v1/day/{MONDAY.isoformat()}/summary")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["day"] == "2026-03-09"
    assert body["timezone"] == "America/New_York"
    assert [t["title"] for t in body["shipped"]] == ["Write Acme proposal", "Late finish"]
    assert [t["title"] for t in body["agents_finished"]] == ["Draft the FAQ"]
    assert body["prepared_by_agents"] == 1
    assert body["queued_overnight"] == []
    assert body["rolls_over"] == [
        {
            "task_id": str(rolling.id),
            "project_id": str(project),
            "title": "Book the venue",
            "label": "human",
            "rollover_count": 0,
            "tonight": 1,
        }
    ]


@pytest.mark.req("Success metrics")
@pytest.mark.wp("P1-18")
async def test_exit_gate_counts_decided_plan_days(
    db: DbUrls, workspace: WorkspaceHandle, session_client: SessionClient, clock: FixedClock
) -> None:
    """Plans on Thursday 5, Friday 6 and Monday 9 March, each with an item the human
    accepted, removed or swapped in; Wednesday's plan was never acted on. As of Monday
    evening the run is three working days (the weekend is skipped); on Monday morning,
    before today's decision, it still ends on Friday."""
    project = await new_project(workspace, clock, "Acme site")
    task = await new_task(
        workspace, clock, project, "Write Acme proposal", label="human", estimate_minutes=30
    )

    def plan(day: str, decision: str | None) -> None:
        plan_id = uuid.uuid4()
        execute(
            db,
            "INSERT INTO daily_plans (id, workspace_id, day, built_at, source, trigger, status,"
            " created_by) VALUES (%s, %s, %s, %s, 'fallback', 'morning', 'published', 'system')",
            plan_id,
            workspace.id,
            day,
            _at(f"{day}T12:30:00"),
        )
        at = _at(f"{day}T12:40:00")
        execute(
            db,
            "INSERT INTO plan_items (workspace_id, plan_id, task_id, position, reason, created_by,"
            " accepted_at, removed_at, swapped_from_task_id)"
            " VALUES (%s, %s, %s, 0, 'due today', 'system', %s, %s, %s)",
            workspace.id,
            plan_id,
            task.id,
            at if decision == "accept" else None,
            at if decision == "remove" else None,
            uuid.uuid4() if decision == "swap" else None,
        )

    plan("2026-03-04", None)
    plan("2026-03-05", "accept")
    plan("2026-03-06", "remove")

    clock.set(_at("2026-03-09T13:00:00"))  # Monday 09:00 local: today not planned yet
    morning = await session_client.get(
        "/v1/metrics/summary", params={"from": "2026-03-02", "to": "2026-03-09"}
    )
    assert morning.status_code == 200, morning.text
    assert morning.json()["plan_days_in_a_row"] == 2
    assert morning.json()["exit_gate_days"] == 10

    plan("2026-03-09", "swap")
    evening = await session_client.get(
        "/v1/metrics/summary", params={"from": "2026-03-02", "to": "2026-03-09"}
    )
    assert evening.json()["plan_days_in_a_row"] == 3
