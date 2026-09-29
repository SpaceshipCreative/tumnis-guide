"""Projects over REST (P0-17, FR-2.1, FR-1.1, FR-5.6): reorder writes one row (or
rebalances past the key limit), archive hides and keeps, a new project gets its default
policy, health comes from the registered stats source."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import QueryCounter, WorkspaceHandle
    from tumnis.modules.projects.tests.conftest import FakeStatsSource, MakeProject

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _create(client: httpx.AsyncClient, name: str, **fields: Any) -> dict[str, Any]:
    response = await client.post("/v1/projects", json={"name": name, **fields})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _names(client: httpx.AsyncClient, **params: Any) -> list[str]:
    response = await client.get("/v1/projects", params=params)
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()["items"]]


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


def _insert_tasks(db: DbUrls, workspace_id: uuid.UUID, project_id: str, n: int) -> int:
    """n task rows in the project through the owner connection. The tasks table arrives
    with P0-18; until then the project has no task rows and this inserts none."""
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        exists = conn.execute("SELECT to_regclass('public.tasks') IS NOT NULL").fetchone()
        if not (exists and exists[0]):
            return 0
        for i in range(n):
            conn.execute(
                "INSERT INTO tasks (workspace_id, project_id, title, status, board_rank)"
                " VALUES (%s, %s, %s, 'backlog', %s)",
                (workspace_id, project_id, f"Task {i}", f"a{i}"),
            )
    return n


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
async def test_reorder_writes_one_row(
    app: FastAPI,
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    query_counter: QueryCounter,
    db: DbUrls,
) -> None:
    """T-P0-17-09
    Given 5 projects, `POST /v1/projects/{p3}/reorder {after_id: p4, before_id: p5}` runs
    exactly one `UPDATE projects` statement that changes one row (the others keep their
    version), and `GET /v1/projects` lists p1, p2, p4, p3, p5.
    """
    from tumnis.core import db as core_db  # noqa: PLC0415

    made = [await _create(session_client, f"p{i}") for i in range(1, 6)]
    p1, p2, p3, p4, p5 = made
    assert await _names(session_client) == ["p1", "p2", "p3", "p4", "p5"]

    query_counter.watch(core_db.app_engine())
    moved = await session_client.post(
        f"/v1/projects/{p3['id']}/reorder",
        json={"after_id": p4["id"], "before_id": p5["id"], "version": p3["version"]},
    )
    assert moved.status_code == 200, moved.text
    updates = [
        s for s in query_counter.statements if s.lstrip().upper().startswith("UPDATE PROJECTS")
    ]
    assert len(updates) == 1, updates
    assert p4["sort_key"] < moved.json()["sort_key"] < p5["sort_key"]

    versions = dict(_owner(db, "SELECT name, version FROM projects"))
    assert versions == {"p1": 1, "p2": 1, "p3": 2, "p4": 1, "p5": 1}
    assert await _names(session_client) == ["p1", "p2", "p4", "p3", "p5"]
    assert {p1["id"], p2["id"]} <= {str(i) for (i,) in _owner(db, "SELECT id FROM projects")}


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
async def test_reorder_rebalances_past_max_key_len(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """T-P0-17-10
    Given two neighbours whose keys are 47 characters, a reorder between them needs a key
    of `MAX_KEY_LEN` or more: every active project is rewritten with `n_keys(n)` in one
    transaction, the order (with the move) is kept, every key is short again, and a
    structlog event `rank.rebalanced` names the table and the row count.
    """
    import structlog  # noqa: PLC0415

    from tumnis.core.rank import MAX_KEY_LEN, n_keys  # noqa: PLC0415

    made = [await _create(session_client, f"p{i}") for i in range(1, 6)]
    p3, p4, p5 = made[2], made[3], made[4]
    stem = "a3" + "0" * 44
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute("UPDATE projects SET sort_key = %s WHERE id = %s", (stem + "1", p4["id"]))
        conn.execute("UPDATE projects SET sort_key = %s WHERE id = %s", (stem + "2", p5["id"]))
    assert len(stem + "1") == 47

    with structlog.testing.capture_logs() as logs:
        moved = await session_client.post(
            f"/v1/projects/{p3['id']}/reorder",
            json={"after_id": p4["id"], "before_id": p5["id"], "version": p3["version"]},
        )
    assert moved.status_code == 200, moved.text
    assert await _names(session_client) == ["p1", "p2", "p4", "p3", "p5"]
    keys = [k for (k,) in _owner(db, "SELECT sort_key FROM projects ORDER BY sort_key")]
    assert keys == n_keys(5)
    assert all(len(k) < MAX_KEY_LEN for k in keys)
    events = [e for e in logs if e["event"] == "rank.rebalanced"]
    assert len(events) == 1, logs
    assert events[0]["table"] == "projects"
    assert events[0]["rows"] == 5


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
async def test_archive_hides_from_lists_and_keeps_data(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """T-P0-17-11
    Given a project with tasks (inserted through the owner session), archiving it hides it
    from `GET /v1/projects`, shows it with `archived_at` under `include_archived=true`,
    keeps `GET /v1/projects/{id}` answering, keeps the task rows and writes one
    `project.archived` outbox row.
    """
    kept = await _create(session_client, "Kept")
    gone = await _create(session_client, "Archived")
    tasks = _insert_tasks(db, workspace.id, gone["id"], 3)

    archived = await session_client.post(
        f"/v1/projects/{gone['id']}/archive", json={"version": gone["version"]}
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None

    assert await _names(session_client) == ["Kept"]
    everything = await session_client.get("/v1/projects", params={"include_archived": "true"})
    assert everything.status_code == 200, everything.text
    rows = {item["name"]: item for item in everything.json()["items"]}
    assert set(rows) == {"Kept", "Archived"}
    assert rows["Archived"]["archived_at"] is not None
    assert rows["Kept"]["archived_at"] is None

    one = await session_client.get(f"/v1/projects/{gone['id']}")
    assert one.status_code == 200, one.text
    assert one.json()["archived_at"] is not None
    assert kept["id"] != gone["id"]

    if tasks:
        [(count,)] = _owner(db, "SELECT count(*) FROM tasks WHERE project_id = %s", (gone["id"],))
        assert count == tasks
    outbox = _owner(
        db,
        "SELECT payload FROM outbox WHERE name = 'project.archived'",
    )
    assert [payload["project_id"] for (payload,) in outbox] == [gone["id"]]


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
async def test_unarchive_restores_to_lists(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle
) -> None:
    """T-P0-17-12
    Archive then unarchive (each with the version it read): the project is back in
    `GET /v1/projects` with `archived_at` null, in its old place; an archive with a stale
    version answers 409 `stale_version`.
    """
    first = await _create(session_client, "First")
    middle = await _create(session_client, "Middle")
    await _create(session_client, "Last")

    archived = await session_client.post(
        f"/v1/projects/{middle['id']}/archive", json={"version": middle["version"]}
    )
    assert archived.status_code == 200, archived.text
    assert await _names(session_client) == ["First", "Last"]

    stale = await session_client.post(
        f"/v1/projects/{first['id']}/archive", json={"version": first["version"] + 1}
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"

    restored = await session_client.post(
        f"/v1/projects/{middle['id']}/unarchive", json={"version": archived.json()["version"]}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None
    assert await _names(session_client) == ["First", "Middle", "Last"]


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P0-17")
async def test_new_project_gets_default_policy_row(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """T-P0-17-17
    `POST /v1/projects` writes one `project_policies` row for the project, holding the
    FR-5.6 gated and allowed lists and the SAF-5 limits (2 concurrent runs, 60-minute
    runs, 20 tasks per run); `projects.api.get_policy` reads the same.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api  # noqa: PLC0415
    from tumnis.modules.projects.rules import ALLOWED_DEFAULT, GATED_DEFAULT  # noqa: PLC0415

    made = await _create(session_client, "With policy")
    rows = _owner(
        db,
        "SELECT project_id, gated, allowed, tool_allowlist, max_concurrent_runs,"
        " max_run_minutes, max_tasks_per_run FROM project_policies",
    )
    assert len(rows) == 1
    project_id, gated, allowed, tools, runs, minutes, per_run = rows[0]
    assert str(project_id) == made["id"]
    assert tuple(gated) == GATED_DEFAULT
    assert tuple(allowed) == ALLOWED_DEFAULT
    assert tools == []
    assert (runs, minutes, per_run) == (2, 60, 20)

    async with tenant_session(workspace.ctx) as s:
        policy = await api.get_policy(s, uuid.UUID(made["id"]))
    assert tuple(policy.gated) == GATED_DEFAULT
    assert tuple(policy.allowed) == ALLOWED_DEFAULT
    assert (policy.max_concurrent_runs, policy.max_run_minutes, policy.max_tasks_per_run) == (
        2,
        60,
        20,
    )


@pytest.mark.req("FR-1.1")
@pytest.mark.wp("P0-17")
@pytest.mark.xfail(strict=True, reason="spec:P0-17")
async def test_health_uses_registered_stats_source(
    app: FastAPI,
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    fake_stats: FakeStatsSource,
    make_project: MakeProject,
) -> None:
    """T-P0-17-19
    A fake `ProjectStatsSource` answering `HealthFacts(waiting_on_human=1, overdue=0)` and
    4 open tasks for one project: `GET /v1/projects/{id}` shows `health` blocked and
    `open_count` 4; the list shows it too, the other projects on track, after one call to
    the source for every id on the page (no N+1).
    """
    from datetime import date  # noqa: PLC0415

    from tumnis.modules.projects.api import ProjectStats  # noqa: PLC0415
    from tumnis.modules.projects.rules import HealthFacts  # noqa: PLC0415

    blocked = await make_project(name="Blocked", deadline=date(2026, 4, 1))
    others = [await make_project(name=f"Fine {i}") for i in range(3)]
    fake_stats.by_project[blocked.id] = ProjectStats(
        health_facts=HealthFacts(waiting_on_human=1, overdue=0),
        open_count=4,
        next_open_due=date(2026, 3, 20),
    )

    one = await session_client.get(f"/v1/projects/{blocked.id}")
    assert one.status_code == 200, one.text
    body = one.json()
    assert body["health"] == "blocked"
    assert body["open_count"] == 4
    assert body["next_milestone"] == "2026-03-20"
    assert body["last_agent_activity_at"] is None

    fake_stats.calls.clear()
    listed = await session_client.get("/v1/projects")
    assert listed.status_code == 200, listed.text
    health = {item["name"]: item["health"] for item in listed.json()["items"]}
    assert health == {"Blocked": "blocked", **{p.name: "on_track" for p in others}}
    assert len(fake_stats.calls) == 1
    assert set(fake_stats.calls[0]) == {blocked.id, *(p.id for p in others)}
