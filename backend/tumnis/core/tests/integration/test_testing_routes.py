"""Test-only routes exist only with fake adapters (P0-04, REL-7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import psycopg
import pytest

from tests._pg import OWNER, DbUrls
from tests.fixtures import settings_for

if TYPE_CHECKING:
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
@pytest.mark.xfail(strict=True, reason="spec:P0-04")
async def test_reset_route_exists_only_with_fakes(db: DbUrls, clock: FixedClock) -> None:
    """T-P0-04-12
    POST /v1/test/reset is 404 with real adapters and 204 with fakes; the reset keeps the
    deployment marker.
    """
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    try:
        for adapters, expected in (("real", 404), ("fake", 204)):
            app = create_app(settings=settings_for(db, tumnis_adapters=adapters), clock=clock)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
                response = await client.post("/v1/test/reset")
            assert response.status_code == expected, (adapters, response.text)
    finally:
        await core_db.dispose()

    with psycopg.connect(db.libpq(OWNER)) as conn:
        assert conn.execute("SELECT env FROM deployment_marker").fetchall() == [("dev",)]
