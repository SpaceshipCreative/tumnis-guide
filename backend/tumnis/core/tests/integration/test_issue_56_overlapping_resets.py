"""Issue #56: a `POST /v1/test/reset` that starts while another is still seeding.

A0.6 resets to the `load` set (2,000 tasks) and times out while that reset is still
seeding; the next test's reset then truncated the tables under the running seed. The two
failed each other with deadlocks, foreign-key and unique violations (a 500 either way).
Waiting for the stale seed instead outlasts the next test's 10 s request timeout. The
latest reset wins: the one still seeding stops at its next record and answers 409.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


class _SlowSink:
    """Hands each record to the real sink 0.2 s late: a seed that takes seconds."""

    def __init__(self, sink: Any) -> None:
        self._sink = sink

    def __getattr__(self, kind: str) -> Any:
        async def write(*args: Any) -> Any:
            await asyncio.sleep(0.2)
            return await getattr(self._sink, kind)(*args)

        return write


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
async def test_issue_56_a_new_reset_supersedes_the_one_seeding(
    db: DbUrls, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first reset seeds slowly; a second starts once the first is seeding. The first
    stops and answers 409, the second answers 204, and the database holds one seeded
    workspace and user."""
    from tumnis.core import testing_routes  # noqa: PLC0415
    from tumnis.seed import load_seed as real_load_seed  # noqa: PLC0415

    first_seeding = asyncio.Event()

    async def load_seed(path: Any, sink: Any, **kwargs: Any) -> Any:
        if first_seeding.is_set():
            return await real_load_seed(path, sink, **kwargs)
        first_seeding.set()
        return await real_load_seed(path, _SlowSink(sink), **kwargs)

    monkeypatch.setattr(testing_routes, "load_seed", load_seed)

    first = asyncio.create_task(client.post("/v1/test/reset"))
    await asyncio.wait_for(first_seeding.wait(), timeout=30)
    second = asyncio.create_task(client.post("/v1/test/reset"))
    responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=120)

    assert [r.status_code for r in responses] == [409, 204], [r.text for r in responses]
    with psycopg.connect(db.libpq(OWNER)) as conn:
        assert conn.execute("SELECT count(*) FROM workspaces").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (1,)
