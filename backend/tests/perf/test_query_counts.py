"""Constant query counts (P0-29, PERF-1): the dashboard, task list, board and typeahead
endpoints send the same number of SQL statements whether the workspace holds the seed set
(3 projects, 30 tasks) or the load set (10 projects, 2,000 tasks). A count that grows
with the rows is an N+1."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fixtures import QueryCounter
    from tumnis.seed import SeedResult

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# (seed workspace, its user, a project in it, a typeahead query its projects match) per
# size: the load set's projects are "Load project 01" to "10", so `ac` would match none.
SIZES = {
    "seed": ("ws_main", "u_scott", "p_acme", "ac"),
    "load": ("ws_load", "u_load", "p_load_01", "lo"),
}

# The dashboard's reads, the project task list, the board and the quick-add typeahead
# (`{p}` is the size's project, `{q}` its typeahead query).
ENDPOINTS = {
    "projects": "/v1/projects",
    "today": "/v1/tasks?status=today&order=today&limit=5",
    "project_tasks": "/v1/tasks?project_id={p}",
    "board": "/v1/projects/{p}/board",
    "typeahead": "/v1/typeahead/projects?q={q}",
}

# Statements per request, the session check included (its lookup, then `set_config` and
# the endpoint's own queries). Measured in CI (PR #88): projects 6 and board 9 at both
# sizes; the others within their ceilings. A count above its ceiling, or one that differs
# between the sizes, is a new query per row.
CEILINGS = {
    "projects": 6,
    "today": 5,
    "project_tasks": 5,
    "board": 9,
    "typeahead": 4,
}


@pytest.mark.req("PERF-1")
@pytest.mark.wp("P0-29")
async def test_query_count_is_constant(
    app: FastAPI,
    seed: SeedResult,
    load_fixture: SeedResult,
    query_counter: QueryCounter,
) -> None:
    """T-P0-29-07
    Each of the five endpoints, signed in to the seed workspace and then to the load
    workspace, answers 200 and sends as many statements at load size as at seed size, and
    no more than its ceiling.
    """
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import signed_in  # noqa: PLC0415

    # The fixed test clock never refills the per-principal bucket; this counts queries.
    app.state.rate_limiter = None
    query_counter.watch(core_db.app_engine())
    counts: dict[str, dict[str, int]] = {name: {} for name in ENDPOINTS}
    for size, result in (("seed", seed), ("load", load_fixture)):
        workspace, user, project, typed = SIZES[size]
        async with signed_in(app, result.ids[workspace], result.ids[user]) as http:
            for name, path in ENDPOINTS.items():
                url = path.format(p=result.ids[project], q=typed)
                (await http.get(url)).raise_for_status()  # warm: caches, prepared plans
                query_counter.reset()
                answer = await http.get(url)
                counts[name][size] = query_counter.count
                assert answer.status_code == 200, (url, answer.text)

    assert {name: by["load"] for name, by in counts.items()} == {
        name: by["seed"] for name, by in counts.items()
    }, counts
    over = {name: by for name, by in counts.items() if by["load"] > CEILINGS[name]}
    assert not over, over
