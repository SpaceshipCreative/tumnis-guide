"""The worker follows the test clock (Scott decision 86, fakes only; A1.6): `POST
/v1/test/clock` also stores the instant it fixes in the fake-script store, and
`fake_scripts.worker_now()` (what the worker stamps a run's end with) answers it while the
store is enabled. With nothing fixed, after a reset, or with the store off (every real
deployment) it is the system clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

FIXED = datetime(2026, 3, 9, 12, 30, tzinfo=UTC)
NEAR = timedelta(minutes=5)


def _is_system_time(at: datetime) -> bool:
    return abs(at - datetime.now(UTC)) < NEAR


@pytest.mark.req("REL-7")
@pytest.mark.wp("P1-18")
async def test_worker_now_follows_the_fixed_test_clock(client: httpx.AsyncClient) -> None:
    from tumnis.core import fake_scripts  # noqa: PLC0415

    fake_scripts.enable()
    try:
        assert _is_system_time(await fake_scripts.worker_now())

        fixed = await client.post("/v1/test/clock", json={"time": FIXED.isoformat()})
        assert fixed.status_code == 200, fixed.text
        assert await fake_scripts.worker_now() == FIXED

        moved = await client.post("/v1/test/clock", json={"advance_seconds": 90})
        assert moved.status_code == 200, moved.text
        assert await fake_scripts.worker_now() == FIXED + timedelta(seconds=90)

        fake_scripts.disable()
        assert _is_system_time(await fake_scripts.worker_now())
        fake_scripts.enable()

        reset = await client.post("/v1/test/reset")
        assert reset.status_code == 204, reset.text
        assert _is_system_time(await fake_scripts.worker_now())
    finally:
        fake_scripts.disable()
