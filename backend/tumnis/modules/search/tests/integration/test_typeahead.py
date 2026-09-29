"""The project and task typeaheads (P0-20, FR-3.9, PERF-1): one query per call whatever the
workspace's size, inside the quick-add budget, and archived projects leave them."""

from __future__ import annotations

import statistics
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.search.tests.integration import _search

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import QueryCounter
    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

P95_BUDGET_S = 0.100  # plan default: the quick-add typeahead budget


@pytest.mark.req("FR-3.9", "PERF-1")
@pytest.mark.wp("P0-20")
@pytest.mark.xfail(strict=True, reason="spec:P0-20")
async def test_typeahead_query_count_is_fixed(
    search_db: DbUrls,
    seed: SeedResult,
    load_fixture: SeedResult,
    query_counter: QueryCounter,
    clock: FixedClock,
) -> None:
    """T-P0-20-11
    With the seed set (3 projects, 30 tasks) and the load set (10 projects, 2,000 tasks)
    indexed, `typeahead_projects("ac")` and `typeahead_tasks("inv")` each send the same
    number of statements in both workspaces: 2 (`set_config` plus the query). Both find
    something at both sizes.
    """
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.search import api  # noqa: PLC0415

    assert await _search.index_outbox(search_db) >= 2_000
    query_counter.watch(core_db.app_engine())
    counts: dict[tuple[str, str], int] = {}
    for size, result, key, project_q in (
        ("seed", seed, "ws_main", "ac"),
        ("load", load_fixture, "ws_load", "lo"),
    ):
        ctx = WorkspaceContext(result.ids[key], SYSTEM_ACTOR)
        calls: dict[str, Any] = {
            "projects": lambda s, q=project_q: api.typeahead_projects(s, q, now=clock.now()),
            "tasks": lambda s: api.typeahead_tasks(s, "inv", None, now=clock.now()),
        }
        for kind, call in calls.items():
            query_counter.reset()
            async with tenant_session(ctx) as s:
                hits = await call(s)
            counts[size, kind] = query_counter.count
            assert hits, (size, kind)
            assert len(hits) <= 8
            assert {h.entity_type for h in hits} == {kind.rstrip("s")}

    assert counts == {
        ("seed", "projects"): 2,
        ("seed", "tasks"): 2,
        ("load", "projects"): 2,
        ("load", "tasks"): 2,
    }


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P0-20")
async def test_typeahead_latency_on_load_fixture(
    app: FastAPI, db: DbUrls, load_fixture: SeedResult
) -> None:
    """T-P0-20-12
    On the indexed load set, 50 sequential `GET /v1/typeahead/tasks` and
    `/v1/typeahead/projects` calls (alternating, varied prefixes) through the ASGI client,
    signed in as the load user: every answer is 200 and the p95 is under 100 ms (plan
    default). Two warm-up calls are not counted.
    """
    from tumnis.core.tests.integration._audit import signed_in  # noqa: PLC0415

    await _search.index_outbox(db)
    prefixes = ("inv", "dra", "rev", "sen", "pla", "rep", "upd", "est", "roa", "new")
    async with signed_in(app, load_fixture.ids["ws_load"], load_fixture.ids["u_load"]) as http:
        for warm in ("inv", "lo"):
            (await http.get("/v1/typeahead/tasks", params={"q": warm})).raise_for_status()
        timings: list[float] = []
        for i in range(50):
            kind = "tasks" if i % 2 == 0 else "projects"
            started = time.perf_counter()
            answer = await http.get(f"/v1/typeahead/{kind}", params={"q": prefixes[i % 10]})
            timings.append(time.perf_counter() - started)
            assert answer.status_code == 200, answer.text

    p95 = statistics.quantiles(timings, n=20)[18]
    assert p95 < P95_BUDGET_S, f"p95 {p95 * 1000:.1f} ms"


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
@pytest.mark.xfail(strict=True, reason="spec:P0-20")
async def test_archived_projects_leave_the_typeahead(
    db: DbUrls, dbos: type[DBOS], session_client: SessionClient, clock: FixedClock
) -> None:
    """T-P0-20-13
    "Acme rebrand" is created; after the relay drains, `GET /v1/typeahead/projects?q=ac`
    lists it (a project hit, `project_id` its own id). Archived, it leaves the typeahead;
    unarchived, it is back. A task typeahead never lists a project.
    """

    async def typeahead(kind: str, q: str) -> list[dict[str, Any]]:
        answer = await session_client.get(f"/v1/typeahead/{kind}", params={"q": q})
        assert answer.status_code == 200, answer.text
        hits: list[dict[str, Any]] = answer.json()
        return hits

    created = await session_client.post("/v1/projects", json={"name": "Acme rebrand"})
    assert created.status_code == 201, created.text
    project = created.json()
    await _search.drain(db)
    [hit] = await typeahead("projects", "ac")
    assert (hit["entity_type"], hit["entity_id"], hit["project_id"]) == (
        "project",
        project["id"],
        project["id"],
    )
    assert hit["title"] == "Acme rebrand"
    assert await typeahead("tasks", "ac") == []

    clock.advance(timedelta(minutes=1))
    archived = await session_client.post(
        f"/v1/projects/{project['id']}/archive", json={"version": project["version"]}
    )
    assert archived.status_code == 200, archived.text
    await _search.drain(db)
    assert await typeahead("projects", "ac") == []

    clock.advance(timedelta(minutes=1))
    restored = await session_client.post(
        f"/v1/projects/{project['id']}/unarchive", json={"version": archived.json()["version"]}
    )
    assert restored.status_code == 200, restored.text
    await _search.drain(db)
    assert [h["entity_id"] for h in await typeahead("projects", "ac")] == [project["id"]]
