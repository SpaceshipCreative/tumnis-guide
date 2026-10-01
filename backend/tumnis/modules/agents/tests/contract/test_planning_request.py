"""The planning request the master's `plan` skill reads (P1-11, FR-4.3, R-02): the body of
the plan packet, assembled from what the planning module gathers (the day, its working
window, free blocks and events, the candidate tasks in order) and what agents adds (each
candidate project's brief excerpt and health, and the master's registry of project
agents). Pure: `packet_builder.assemble_planning_request`, the half `agents.api.
planning_request` calls once it has read the projects, briefs and registry."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from tumnis.core.types import Interval

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.contract]

DAY = date(2026, 3, 9)
NOW = datetime(2026, 3, 9, 12, 30, tzinfo=UTC)
ACME = UUID("0199aa00-0000-7000-8000-000000000a01")
BETA = UUID("0199aa00-0000-7000-8000-000000000b01")


def _task(n: int, project: UUID) -> Any:
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    created = NOW - timedelta(days=n)
    return tasks.TaskOut(
        id=UUID(f"0199aa00-0000-7000-8000-{n:012x}"),
        project_id=project,
        parent_id=None,
        title=f"Task {n}",
        label="human" if n % 3 else "ai",
        label_source="user",
        label_reason=None,
        label_suggestion=None,
        status="backlog",
        priority="normal",
        due_on=DAY if n % 7 == 0 else None,
        estimate_minutes=30 if n % 3 else None,
        first_action=f"Open task {n}",
        acceptance_criteria=None,
        assigned_agent_id=None,
        column_id=None,
        board_rank="a0",
        rollover_count=n % 4,
        started_at=None,
        completed_at=None,
        actual_minutes=None,
        tainted=False,
        source="user",
        version=1,
        created_at=created,
        updated_at=created,
    )


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
@pytest.mark.xfail(strict=True, reason="spec:P1-11")
def test_planning_request_validates_and_includes_registry(repo_root: Path) -> None:
    """T-P1-11-19
    The built request validates against the planning request schema (version 1), carries the
    free blocks, the first 60 candidates in the order given, each candidate project once with
    its brief cut to the excerpt length, and the master registry.
    """
    from tumnis.modules.agents import packet_builder  # noqa: PLC0415
    from tumnis.modules.agents.skill_io import (  # noqa: PLC0415
        BRIEF_EXCERPT_MAX,
        MAX_CANDIDATES,
        PlanningRequest,
        ProjectAgentEntry,
    )

    window = Interval(datetime(2026, 3, 9, 13, tzinfo=UTC), datetime(2026, 3, 9, 22, tzinfo=UTC))
    free = [
        Interval(datetime(2026, 3, 9, 14, tzinfo=UTC), datetime(2026, 3, 9, 16, tzinfo=UTC)),
        Interval(datetime(2026, 3, 9, 17, tzinfo=UTC), datetime(2026, 3, 9, 19, tzinfo=UTC)),
    ]
    events = [("Acme kickoff", window.start, free[0].start)]
    candidates = [_task(n, ACME if n % 2 else BETA) for n in range(1, 71)]
    projects = [
        packet_builder.PlanningProjectIn(
            id=ACME,
            name="Acme site",
            health="at_risk",
            next_milestone=date(2026, 3, 20),
            brief="A" * 2_000,
        ),
        packet_builder.PlanningProjectIn(
            id=BETA, name="Beta app", health="on_track", next_milestone=None, brief=""
        ),
    ]
    registry = [
        ProjectAgentEntry(
            project_id=ACME,
            project_name="Acme site",
            profile="acme-site",
            status="ready",
            runner="homelab-hermes",
        )
    ]

    request = packet_builder.assemble_planning_request(
        day=DAY,
        timezone="America/New_York",
        now=NOW,
        window=window,
        free_blocks=free,
        events=events,
        candidates=candidates,
        projects=projects,
        agents=registry,
    )

    assert isinstance(request, PlanningRequest)
    body = request.model_dump(mode="json")
    schema = json.loads((repo_root / "schemas/planning/v1/request.json").read_text())
    Draft202012Validator(schema).validate(body)
    assert body["schema_version"] == 1
    assert body["day"] == "2026-03-09"
    assert [(b["start"], b["end"]) for b in body["free_blocks"]] == [
        ("2026-03-09T14:00:00Z", "2026-03-09T16:00:00Z"),
        ("2026-03-09T17:00:00Z", "2026-03-09T19:00:00Z"),
    ]
    assert body["working_window"] == {
        "start": "2026-03-09T13:00:00Z",
        "end": "2026-03-09T22:00:00Z",
    }
    assert len(body["candidates"]) == MAX_CANDIDATES == 60
    assert [c["task_id"] for c in body["candidates"]] == [str(t.id) for t in candidates[:60]]
    first = body["candidates"][0]
    assert first["project_name"] == "Acme site"
    assert first["age_days"] == 1
    briefs = {p["id"]: p for p in body["projects"]}
    assert set(briefs) == {str(ACME), str(BETA)}
    assert briefs[str(ACME)]["brief_excerpt"] == "A" * BRIEF_EXCERPT_MAX
    assert briefs[str(ACME)]["health"] == "at_risk"
    assert briefs[str(BETA)]["brief_excerpt"] == ""
    assert body["agents"] == [
        {
            "project_id": str(ACME),
            "project_name": "Acme site",
            "profile": "acme-site",
            "status": "ready",
            "runner": "homelab-hermes",
        }
    ]
    assert body["events"] == [
        {
            "title": "Acme kickoff",
            "start": "2026-03-09T13:00:00Z",
            "end": "2026-03-09T14:00:00Z",
        }
    ]
