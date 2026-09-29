"""Test-only routes exist only with fake adapters (P0-04, REL-7); `POST /v1/test/clock`
sets the server clock (issue #6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import psycopg
import pytest

from tests._auth import SESSION_COOKIE, password_step, seed_user, totp_code, totp_step
from tests._pg import OWNER, DbUrls
from tests.fixtures import settings_for

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
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


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
async def test_clock_route_exists_only_with_fakes(db: DbUrls, clock: FixedClock) -> None:
    """T-P0-04-14
    POST /v1/test/clock is 404 with real adapters and 200 with fakes (issue #6).
    """
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    body = {"time": "2026-03-09T14:00:00Z"}
    try:
        for adapters, expected in (("real", 404), ("fake", 200)):
            app = create_app(settings=settings_for(db, tumnis_adapters=adapters), clock=clock)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
                response = await client.post("/v1/test/clock", json=body)
            assert response.status_code == expected, (adapters, response.text)
    finally:
        await core_db.dispose()


@pytest.mark.req("REL-7", "REL-6")
@pytest.mark.wp("P0-04")
async def test_clock_route_sets_and_advances_the_server_clock_until_reset(
    app: FastAPI, client: httpx.AsyncClient, clock: FixedClock
) -> None:
    """T-P0-04-15
    `{time}` fixes the app's clock at that instant, `{advance_seconds}` moves it, a body
    with neither or both is 422, and `POST /v1/test/reset` gives the base clock back.
    """
    start = clock.now()
    at = datetime(2026, 3, 9, 14, 0, tzinfo=UTC)

    fixed = await client.post("/v1/test/clock", json={"time": "2026-03-09T10:00:00-04:00"})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json() == {"now": "2026-03-09T14:00:00Z"}
    assert app.state.clock.now() == at

    moved = await client.post("/v1/test/clock", json={"advance_seconds": 90})
    assert moved.status_code == 200, moved.text
    assert app.state.clock.now() == at + timedelta(seconds=90)

    for bad in ({}, {"time": "2026-03-09T14:00:00Z", "advance_seconds": 1}):
        refused = await client.post("/v1/test/clock", json=bad)
        assert refused.status_code == 422, (bad, refused.text)
    naive = await client.post("/v1/test/clock", json={"time": "2026-03-09T14:00:00"})
    assert naive.status_code == 422, naive.text

    reset = await client.post("/v1/test/reset")
    assert reset.status_code == 204, reset.text
    assert app.state.clock.now() == start


@pytest.mark.req("SEC-1", "REL-7")
@pytest.mark.wp("P0-04")
async def test_totp_is_checked_at_the_test_clock(
    client: httpx.AsyncClient, clock: FixedClock
) -> None:
    """T-P0-04-16
    With the server clock set through POST /v1/test/clock, the seed user signs in with the
    TOTP code at that instant (A0.1 computes it at the browser's installed clock), and a
    code at the base clock's time is refused.
    """
    email, password, secret = seed_user()
    at = datetime(2026, 3, 9, 14, 0, tzinfo=UTC)
    assert (await client.post("/v1/test/reset")).status_code == 204
    assert (await client.post("/v1/test/clock", json={"time": at.isoformat()})).status_code == 200

    first = await password_step(client, email, password)
    assert first.status_code == 200, first.text
    wrong = await totp_step(client, first.json()["preauth"], totp_code(secret, clock.now()))
    assert wrong.status_code == 401, wrong.text

    again = await password_step(client, email, password)
    assert again.status_code == 200, again.text
    done = await totp_step(client, again.json()["preauth"], totp_code(secret, at))
    assert done.status_code == 200, done.text
    assert client.cookies.get(SESSION_COOKIE)
