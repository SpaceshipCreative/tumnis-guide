"""Issue #51: `POST /v1/test/reset` must not deadlock with an outbox relay pass.

The relay holds its claim on `outbox` while it asks `modules.enabled` about each
subscriber, and a cache miss reads `module_flags` on another connection. A reset that
locked `module_flags` before `outbox` made that read wait on the reset and the reset wait
on the claim: a deadlock across two connections that Postgres cannot see.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PROBE = "calendar.issue_51_probe"  # calendar is not a required module: enabled() reads


@pytest.fixture
def probe_subscriber() -> Iterator[None]:
    """A calendar subscriber on test.ping, unregistered after the test (the registry is
    process-wide)."""
    events = importlib.import_module("tumnis.core.events")

    async def handler(envelope: Any) -> None:
        return None

    events.subscribe("test.ping", name=PROBE)(handler)
    try:
        yield
    finally:
        events._subscribers.pop(PROBE, None)


def _lock_waiters(db: DbUrls) -> list[str]:
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        rows = conn.execute(
            "SELECT query FROM pg_stat_activity WHERE datname = current_database()"
            " AND wait_event_type = 'Lock'"
        ).fetchall()
    return [str(query) for (query,) in rows]


def _terminate_lock_waiters(db: DbUrls) -> None:
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = current_database() AND pid <> pg_backend_pid()"
            " AND (wait_event_type = 'Lock' OR state = 'idle in transaction')"
        )


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
@pytest.mark.usefixtures("probe_subscriber")
async def test_issue_51_reset_waits_for_relay_claim(
    db: DbUrls, dbos: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reset that starts while a relay pass holds its claim waits for the pass, and
    both finish: the pass reads the module flag it needs and marks the row sent, then the
    reset empties the tables."""
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core import modules  # noqa: PLC0415
    from tumnis.core.events import TestPingV1, relay_once  # noqa: PLC0415
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.testing_routes import truncate_tables  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    ws = make_workspace(db)  # a fresh workspace: its module flags are not cached
    async with tenant_session(WorkspaceContext(ws, SYSTEM_ACTOR)) as session:
        await emit(session, TestPingV1(note="issue 51"), occurred_at=clock.now())

    claimed = asyncio.Event()
    real_enabled = modules.enabled

    async def enabled_once_reset_waits(module: str, workspace_id: Any) -> bool:
        """The relay holds its claim here: let the reset start and queue for its locks
        first, then read the flag as the relay would."""
        claimed.set()
        for _ in range(500):
            if await asyncio.to_thread(_lock_waiters, db):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("the reset never waited for the relay's claim")
        return await real_enabled(module, workspace_id)

    monkeypatch.setattr(modules, "enabled", enabled_once_reset_waits)

    relay = asyncio.create_task(relay_once())
    await asyncio.wait_for(claimed.wait(), timeout=30)
    reset = asyncio.create_task(truncate_tables(db.owner))
    _, pending = await asyncio.wait({relay, reset}, timeout=30)
    if pending:  # free the stuck connections first, so the tasks can unwind
        waiting = await asyncio.to_thread(_lock_waiters, db)
        await asyncio.to_thread(_terminate_lock_waiters, db)
        for task in pending:
            task.cancel()
        await asyncio.wait(pending, timeout=10)
        pytest.fail(f"reset and relay deadlocked (issue #51); waiting on locks: {waiting}")

    relayed, emptied = relay.result(), reset.result()
    assert relayed == 1
    assert "outbox" in emptied
    assert "module_flags" in emptied
    with psycopg.connect(db.libpq(OWNER)) as conn:
        assert conn.execute("SELECT count(*) FROM outbox").fetchone() == (0,)
