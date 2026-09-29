"""A0.3 · Tenant isolation over every route (phase 0 acceptance, committed red by P0-05).

Workspace B sweeps every /v1 route with A's ids and must neither see nor change A's rows.
Parametrized at collection time over the computed route inventory (caller x route), so
a route added by a later WP joins the sweep with no edit here. Backed by the table-level
RLS suite in backend/tests/isolation/ (P0-06).

Turns green with P0-06 (RLS on every table), P0-10 (route walker), P0-14 (key and
session clients).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests._pg import OWNER, DbUrls
from tests.acceptance._isolation import (
    all_ids,
    client_for,
    collection_cases,
    find_case,
    leaked,
    modules_with_routes,
    requests_for,
    response_json,
    route_inventory,
    row_ids,
    snapshot,
    workspace_id,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.req("Hosted readiness", "ADR-0009"),
    pytest.mark.wp("P0-05"),
    pytest.mark.xfail(strict=True, reason="spec:P0-05"),
]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "route_case" in metafunc.fixturenames:
        cases = collection_cases()
        metafunc.parametrize("route_case", cases, ids=[f"{c}-{m} {p}" for c, m, p in cases])


async def test_workspace_b_cannot_touch_workspace_a(  # noqa: PLR0917
    route_case: tuple[str, str, str],
    app: Any,
    db: DbUrls,
    two_workspaces: tuple[Any, Any],
    session_client: Any,
    key_client: Any,
) -> None:
    """T-P0-05-03
    Given workspaces A and B with rows in every tenant table, when B (by session and by an
    API key with every scope) calls each /v1 route with A's ids in the path, in every id
    filter and in a valid request body, then every read or write naming an A row is 404
    (not 403), no response carries A's rows, and no A row changed.
    """
    caller, method, path = route_case
    a, _b = two_workspaces
    owner = db.libpq(OWNER)

    # 1. The inventory is computed from app.routes and covers every module with routes.
    inventory = route_inventory(app)
    assert inventory, "the /v1 route inventory is empty"
    missing = modules_with_routes() - {case.module for case in inventory}
    assert not missing, f"modules with routes but none in the sweep: {sorted(missing)}"
    case = find_case(app, method, path)

    client = await client_for(caller, app, session_client, key_client)
    a_rows = row_ids(owner, a)
    a_ids = all_ids(owner, a)
    assert workspace_id(a) in a_ids
    before = snapshot(owner, a)

    # 2 to 4. Row routes with A's ids, list routes plain and filtered by A's ids, writes
    # with a valid body aimed at A's rows.
    for request in requests_for(case, a_rows):
        response = await client.request(
            request.method, request.url, params=request.params, json=request.body
        )
        where = f"{caller} {request.method} {request.url} {request.params}"
        if request.kind == "row":
            assert response.status_code == 404, f"{where}: {response.status_code}"
        elif request.kind == "list":
            assert response.status_code in (200, 404), f"{where}: {response.status_code}"
        else:
            assert response.status_code < 500, f"{where}: {response.status_code}"
        if response.is_success:
            assert not leaked(response_json(response), a_ids), f"{where} returned A's rows"

    # 5. Nothing of A's changed (every column, versions included).
    assert snapshot(owner, a) == before, f"{caller} {method} {path} changed an A row"
