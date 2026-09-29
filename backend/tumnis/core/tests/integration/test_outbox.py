"""emit() writes the outbox row and the NOTIFY in the caller's transaction (P0-07,
ADR-0011). Uses the harness tenant table `tenant_probe` as "the module's own write"."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from sqlalchemy import text

from tests._pg import APP, OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

AT = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


class BoomError(Exception):
    pass


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


async def _listener(db: DbUrls) -> psycopg.AsyncConnection[Any]:
    conn = await psycopg.AsyncConnection.connect(db.libpq(APP), autocommit=True)
    await conn.execute("LISTEN outbox")
    return conn


async def _drain(conn: psycopg.AsyncConnection[Any], seconds: float) -> list[psycopg.Notify]:
    return [n async for n in conn.notifies(timeout=seconds)]


def _outbox_rows(db: DbUrls) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        cur = conn.execute("SELECT * FROM outbox ORDER BY id")
        names = [c.name for c in cur.description or ()]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


@pytest.mark.req("ADR-0011")
@pytest.mark.wp("P0-07")
@pytest.mark.usefixtures("core_db")
async def test_rolled_back_write_leaves_no_outbox_row(
    db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P0-07-01
    A write plus `emit` whose transaction raises leaves no row and no NOTIFY.
    """
    from tumnis.core.events import TestPingV1  # noqa: PLC0415
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    listener = await _listener(db)
    try:

        async def write_then_fail() -> None:
            async with tenant_session(workspace.ctx) as session:
                await session.execute(text("INSERT INTO tenant_probe (name) VALUES ('rolled')"))
                await emit(session, TestPingV1(note="never"), occurred_at=AT)
                raise BoomError

        with pytest.raises(BoomError):
            await write_then_fail()
        assert await _drain(listener, 0.5) == []
    finally:
        await listener.close()

    assert _outbox_rows(db) == []
    with psycopg.connect(db.libpq(OWNER)) as conn:
        assert conn.execute("SELECT count(*) FROM tenant_probe").fetchone() == (0,)


@pytest.mark.req("ADR-0011")
@pytest.mark.wp("P0-07")
@pytest.mark.usefixtures("core_db")
async def test_committed_write_inserts_one_row_and_notifies(
    db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P0-07-02
    Commit gives one row with the envelope fields and one notification on channel `outbox`.
    """
    from tumnis.core.events import TestPingV1  # noqa: PLC0415
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    listener = await _listener(db)
    try:
        async with tenant_session(workspace.ctx) as session:
            await session.execute(text("INSERT INTO tenant_probe (name) VALUES ('kept')"))
            event_id = await emit(session, TestPingV1(note="hello"), occurred_at=AT)
        notes = await _drain(listener, 0.5)
    finally:
        await listener.close()

    assert [n.channel for n in notes] == ["outbox"]
    rows = _outbox_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == event_id
    assert row["workspace_id"] == workspace.id
    assert row["name"] == "test.ping"
    assert row["schema_version"] == 1
    assert row["actor"] == "system"
    assert row["occurred_at"] == AT
    assert row["payload"] == {"schema_version": 1, "note": "hello"}
    assert row["trace_context"] == {}
    assert row["sent_at"] is None
