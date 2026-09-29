"""Tasks through REST (P0-18): every FR-3.1 field round-trips, agents must estimate Human
and Hybrid work (FR-4.4), Done records the wall minutes since the task started (FR-4.4),
and a task reaches outside content only through a ContextItem (FR-14.2)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.tasks.tests.conftest import owner_rows

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import KeyClientFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.tasks.tests.conftest import Actors, MakeProject, MakeTask

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-3.1")
@pytest.mark.wp("P0-18")
async def test_fr_3_1_fields_round_trip(
    app: FastAPI, session_client: SessionClient, make_task: MakeTask
) -> None:
    """T-P0-18-16
    `POST /v1/tasks` with every FR-3.1 field (project, parent, title, label, priority, due
    date, estimate, first action, acceptance criteria, status) answers 201; `GET
    /v1/tasks/{id}` and the project's list return each field as sent, with the label's
    source (`user`), a board column and rank, no assigned agent and the version.
    """
    parent = await make_task(label="hybrid", estimate_minutes=90)
    sent = {
        "project_id": str(parent.project_id),
        "parent_id": str(parent.id),
        "title": "Pick clear-space rules",
        "label": "hybrid",
        "priority": "high",
        "due_on": "2026-03-12",
        "estimate_minutes": 45,
        "first_action": "Measure the mark's cap height",
        "acceptance_criteria": "Rules cover print and screen",
        "status": "today",
    }
    created = await session_client.post("/v1/tasks", json=sent)
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    got = await session_client.get(f"/v1/tasks/{task_id}")
    assert got.status_code == 200, got.text
    body = got.json()
    for field, value in sent.items():
        assert body[field] == value, field
    assert body["label_source"] == "user"
    assert body["assigned_agent_id"] is None
    assert body["column_id"] is not None
    assert isinstance(body["board_rank"], str)
    assert body["version"] == created.json()["version"]
    assert (body["rollover_count"], body["actual_minutes"]) == (0, None)

    listed = await session_client.get("/v1/tasks", params={"project_id": str(parent.project_id)})
    assert listed.status_code == 200, listed.text
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert set(by_id) == {str(parent.id), task_id}
    assert by_id[task_id] == body


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P0-18")
async def test_agent_create_without_estimate_returns_422(
    app: FastAPI, key_client: KeyClientFactory, make_project: MakeProject
) -> None:
    """T-P0-18-08
    A key with `tasks:write` creating a Human or Hybrid task without an estimate gets 422
    `estimate_required` and no row is written; with an estimate it gets 201; an AI task's
    estimate is dropped; a pending label needs none.
    """
    project = await make_project()
    client = await key_client(frozenset({"tasks:write"}))
    base = {"project_id": str(project.id), "title": "Agent work"}
    async with client:
        for label in ("human", "hybrid"):
            refused = await client.post("/v1/tasks", json={**base, "label": label})
            assert refused.status_code == 422, refused.text
            assert refused.json()["code"] == "estimate_required"
        estimated = await client.post(
            "/v1/tasks", json={**base, "label": "human", "estimate_minutes": 20}
        )
        ai = await client.post("/v1/tasks", json={**base, "label": "ai", "estimate_minutes": 20})
        pending = await client.post("/v1/tasks", json=base)
    assert estimated.status_code == 201, estimated.text
    assert estimated.json()["estimate_minutes"] == 20
    assert ai.status_code == 201, ai.text
    assert ai.json()["estimate_minutes"] is None
    assert pending.status_code == 201, pending.text
    assert pending.json()["label"] is None
    assert estimated.json()["label_source"] == "agent"


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P0-18")
async def test_actual_minutes_recorded_on_done(
    app: FastAPI,
    session_client: SessionClient,
    key_client: KeyClientFactory,
    make_task: MakeTask,
    clock: FixedClock,
) -> None:
    """T-P0-18-12
    A Human task started at the clock's time and marked done 95 minutes later has
    `actual_minutes == 95`, `started_at` and `completed_at` set. An AI task through
    in_review to done has `actual_minutes` null.
    """

    async def move(client: SessionClient, task: dict[str, object], to: str) -> dict[str, object]:
        answer = await client.post(
            f"/v1/tasks/{task['id']}/status", json={"to": to, "version": task["version"]}
        )
        assert answer.status_code == 200, answer.text
        moved: dict[str, object] = answer.json()
        return moved

    human = (await make_task(label="human", estimate_minutes=60)).model_dump(mode="json")
    started = await move(session_client, human, "in_progress")
    assert datetime.fromisoformat(str(started["started_at"])) == clock.now()
    clock.advance(timedelta(minutes=95))
    done = await move(session_client, started, "done")
    assert done["actual_minutes"] == 95
    assert datetime.fromisoformat(str(done["completed_at"])) == clock.now()

    ai = (await make_task(label="ai")).model_dump(mode="json")
    ai = await move(session_client, ai, "in_progress")
    agent = await key_client(frozenset({"tasks:read", "tasks:write"}))
    async with agent:
        ai = await move(agent, ai, "in_review")  # type: ignore[arg-type]
    clock.advance(timedelta(minutes=30))
    ai = await move(session_client, ai, "done")
    assert ai["completed_at"] is not None
    assert ai["actual_minutes"] is None


@pytest.mark.req("FR-14.2")
@pytest.mark.wp("P0-18")
async def test_context_item_link_is_the_only_outside_link(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    make_task: MakeTask,
    workspace: WorkspaceHandle,
    actors: Actors,
    db: DbUrls,
) -> None:
    """T-P0-18-17
    A task links outside content by ContextItem id: `POST /v1/tasks/{id}/context-items
    {context_item_id}` answers 201 with the link (twice: still one row); an unknown id is
    404. The tasks tables reference no provider row: their foreign keys reach only
    workspaces, projects, tasks, board columns and context items.
    """
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415

    task = await make_task(label="human", estimate_minutes=30)
    item = await integrations.link_context(
        WorkspaceContext(workspace.id, actors.human),
        owner_type="task",
        owner_id=task.id,
        target_type="url",
        target_url="https://example.com/brief",
        added_by=actors.human,
    )
    url = f"/v1/tasks/{task.id}/context-items"
    for _ in range(2):
        linked = await session_client.post(url, json={"context_item_id": str(item.id)})
        assert linked.status_code == 201, linked.text
        assert linked.json()["context_item_id"] == str(item.id)
        assert linked.json()["target_url"] == "https://example.com/brief"
    assert owner_rows(
        db,
        "SELECT count(*) FROM task_context_items WHERE task_id = %s AND deleted_at IS NULL",
        (task.id,),
    ) == [(1,)]

    missing = await session_client.post(url, json={"context_item_id": str(uuid.uuid4())})
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "not_found"

    targets = owner_rows(
        db,
        "SELECT DISTINCT ref.relname FROM pg_constraint c"
        " JOIN pg_class t ON t.oid = c.conrelid JOIN pg_class ref ON ref.oid = c.confrelid"
        " WHERE c.contype = 'f' AND t.relname = ANY(%s)",
        (["tasks", "task_comments", "task_context_items", "board_columns", "review_items"],),
    )
    assert {name for (name,) in targets} <= {
        "workspaces",
        "projects",
        "tasks",
        "board_columns",
        "context_items",
    }
    assert ("context_items",) in targets
