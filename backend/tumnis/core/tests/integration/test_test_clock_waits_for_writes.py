"""`POST /v1/test/clock` lets the writes that arrived before it finish first (A2.6, J8).

A journey presses Start and moves the server clock at once. The Start request reaches the
api first, but its handler reads the clock only after authentication, idempotency and the
session: a clock change that overtook it there stamped the write with the new time, so
the focus session started 25 minutes late. With fakes, a clock change now waits (briefly)
for the writes already being served; a real deployment has neither the test routes nor
the tracking.
"""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import APIRouter, Request

from tests._pg import DbUrls
from tests.fixtures import settings_for
from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

BEFORE = datetime(2026, 3, 11, 14, 0, tzinfo=UTC)
AFTER = datetime(2026, 3, 11, 14, 25, tzinfo=UTC)


def _slow_router() -> APIRouter:
    from tumnis.core.routing import RoutePolicy, route_policy, v1_router  # noqa: PLC0415

    slow = v1_router("slow-write")

    @slow.post("/slow-write")
    @route_policy(RoutePolicy(auth="none", idempotent=False, not_idempotent_reason="test"))
    async def slow_write(request: Request) -> dict[str, str]:
        await asyncio.sleep(0.3)  # authentication, idempotency, the session...
        return {"now": request.app.state.clock.now().isoformat()}

    return slow


@pytest.mark.req("REL-7")
@pytest.mark.wp("P2-15")
async def test_a_clock_change_waits_for_a_write_already_in_flight(
    db: DbUrls, clock: FixedClock
) -> None:
    """A slow write arrives at 10:00 and reads the clock 300 ms later; the test moves the
    clock to 10:25 while it runs: the write still reads 10:00, then the clock reads 10:25."""
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    app = create_app(
        settings=settings_for(db, tumnis_adapters="fake"),
        clock=clock,
        extra_routers=[_slow_router()],
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            fixed = await client.post("/v1/test/clock", json={"time": BEFORE.isoformat()})
            assert fixed.status_code == 200, fixed.text
            write = asyncio.create_task(client.post("/v1/slow-write"))
            await asyncio.sleep(0.05)  # the write is in its handler
            moved = await client.post("/v1/test/clock", json={"time": AFTER.isoformat()})
            written = await write
    finally:
        await core_db.dispose()

    assert written.status_code == 200, written.text
    assert datetime.fromisoformat(written.json()["now"]) == BEFORE
    assert moved.status_code == 200, moved.text
    assert app.state.clock.now() == AFTER


@pytest.mark.req("REL-7")
@pytest.mark.wp("P2-15")
async def test_a_real_deployment_does_not_track_writes(db: DbUrls, clock: FixedClock) -> None:
    """Without fakes there is no test clock route, so nothing tracks the writes either."""
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.testing_writes import (  # noqa: PLC0415
        WritesInFlight,
        WritesInFlightMiddleware,
    )

    try:
        app = create_app(settings=settings_for(db, tumnis_adapters="real"), clock=clock)
        assert not isinstance(getattr(app.state, "writes_in_flight", None), WritesInFlight)
        classes: list[object] = [m.cls for m in app.user_middleware]
        assert WritesInFlightMiddleware not in classes
    finally:
        await core_db.dispose()
