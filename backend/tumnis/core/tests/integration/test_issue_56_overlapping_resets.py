"""Issue #56: a `POST /v1/test/reset` that starts while another is still seeding.

A0.6 resets to the `load` set (2,000 tasks) and times out while that reset is still
seeding; the next test's reset then truncated the tables under the running seed. The two
failed each other with deadlocks, foreign-key and unique violations (a 500 either way).
Resets must run one at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
async def test_issue_56_a_reset_waits_for_the_one_seeding(
    db: DbUrls, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first reset pauses at the start of its seed until the second has emptied the
    tables (or 2 s have passed, when the second waits its turn). Both answer 204 and the
    database holds one seeded workspace and user."""
    from tumnis.core import testing_routes  # noqa: PLC0415
    from tumnis.seed import load_seed as real_load_seed  # noqa: PLC0415

    real_truncate = testing_routes.truncate_tables
    truncations = 0
    second_truncated = asyncio.Event()
    first_seeding = asyncio.Event()

    async def truncate_tables(owner_url: str) -> list[str]:
        nonlocal truncations
        truncations += 1
        emptied = await real_truncate(owner_url)
        if truncations == 2:
            second_truncated.set()
        return emptied

    async def load_seed(*args: Any, **kwargs: Any) -> Any:
        if not first_seeding.is_set():
            first_seeding.set()
            # The second reset empties the tables here, unless it waits its turn.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(second_truncated.wait(), timeout=2)
        return await real_load_seed(*args, **kwargs)

    monkeypatch.setattr(testing_routes, "truncate_tables", truncate_tables)
    monkeypatch.setattr(testing_routes, "load_seed", load_seed)

    first = asyncio.create_task(client.post("/v1/test/reset"))
    await asyncio.wait_for(first_seeding.wait(), timeout=30)
    second = asyncio.create_task(client.post("/v1/test/reset"))
    responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=120)

    assert [r.status_code for r in responses] == [204, 204], [r.text for r in responses]
    with psycopg.connect(db.libpq(OWNER)) as conn:
        assert conn.execute("SELECT count(*) FROM workspaces").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (1,)
