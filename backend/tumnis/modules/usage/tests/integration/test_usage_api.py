"""Edges of the usage api and route beyond the spec table (P0-21)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.usage.tests.integration import _usage

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-21")
async def test_event_outside_the_map_records_nothing(
    db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """An event the map does not count writes no ledger row and raises no counter."""
    from tests.fixtures import make_envelope  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.usage.api import record  # noqa: PLC0415

    env = make_envelope("test.ping", {"note": "x"}, workspace)
    async with _usage.configured(db), tenant_session(workspace.ctx) as session:
        assert await record(session, env) == 0
    assert _usage.ledger_rows(db, workspace.id, env.event_id) == 0


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-21")
async def test_route_refuses_a_reversed_range_and_answers_404_when_usage_is_off(
    app: FastAPI, workspace: WorkspaceHandle
) -> None:
    """`to` before `from` is 422 `invalid_range`; with the module off for the deployment
    the route is 404 `not_found`."""
    from tumnis.core import modules  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import signed_in  # noqa: PLC0415

    async with signed_in(app, workspace.id, uuid.uuid4()) as client:
        reversed_range = await client.get(
            "/v1/usage", params={"from": "2026-03-09", "to": "2026-03-08"}
        )
        assert reversed_range.status_code == 422
        assert reversed_range.json()["code"] == "invalid_range"

        modules.set_deployment_disabled(frozenset({"usage"}))
        try:
            off = await client.get("/v1/usage", params={"from": "2026-03-08", "to": "2026-03-09"})
        finally:
            modules.set_deployment_disabled(frozenset())
        assert off.status_code == 404
        assert off.json()["code"] == "not_found"
