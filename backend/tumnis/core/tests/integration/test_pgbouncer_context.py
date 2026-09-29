"""PgBouncer transaction pooling: the transaction-local workspace setting cannot leak to the
next client on the same server connection (P0-06, ADR-0009, PERF-1). The `pgbouncer`
fixture runs with `default_pool_size = 1`, so both clients share one backend."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from sqlalchemy import text

from tests._pg import APP

if TYPE_CHECKING:
    from uuid import UUID

    from tests._pg import DbUrls
    from tests.fixtures import PgBouncer

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _two_clients(
    pgbouncer: PgBouncer, db: DbUrls, workspace_id: UUID, *, is_local: bool
) -> tuple[int, tuple[Any, ...]]:
    """c1 sets the workspace (local or session) and commits; then c2, on the same pooled
    backend, reads its PID, the workspace it sees and the workspaces it can read."""
    dsn = pgbouncer.libpq(APP, db.name)
    with (
        psycopg.connect(dsn, autocommit=True) as c1,
        psycopg.connect(dsn, autocommit=True) as c2,
    ):
        with c1.transaction():
            c1.execute(
                "SELECT set_config('app.workspace_id', %s, %s)", (str(workspace_id), is_local)
            )
            row = c1.execute("SELECT pg_backend_pid()").fetchone()
            assert row is not None
            pid1 = row[0]
        with c2.transaction():
            seen = c2.execute(
                "SELECT pg_backend_pid(), app.current_workspace_id(), count(*) FROM workspaces"
            ).fetchone()
            assert seen is not None
    return pid1, tuple(seen)


@pytest.mark.req("ADR-0009", "PERF-1")
@pytest.mark.wp("P0-06")
def test_local_setting_does_not_leak_across_pooled_transactions(
    pgbouncer: PgBouncer, db: DbUrls
) -> None:
    """T-P0-06-07
    Two clients through PgBouncer (pool size 1) share a backend PID; the second transaction
    sees no workspace and no rows.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415

    ws = make_workspace(db)
    pid1, (pid2, workspace, count) = _two_clients(pgbouncer, db, ws, is_local=True)
    assert pid1 == pid2
    assert workspace is None
    assert count == 0


@pytest.mark.req("ADR-0009", "PERF-1")
@pytest.mark.wp("P0-06")
def test_session_setting_would_leak_control(pgbouncer: PgBouncer, db: DbUrls) -> None:
    """T-P0-06-08
    Negative control: with `is_local = false` the second client does see the value,
    proving the test can detect a leak.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415

    ws = make_workspace(db)
    pid1, (pid2, workspace, count) = _two_clients(pgbouncer, db, ws, is_local=False)
    assert pid1 == pid2
    assert workspace == ws
    assert count == 1


@pytest.mark.req("PERF-1", "ADR-0009")
@pytest.mark.wp("P0-06")
async def test_pooled_engine_through_pgbouncer_keeps_each_transaction_in_its_workspace(
    pgbouncer: PgBouncer, db: DbUrls
) -> None:
    """The production engine settings (pooled, psycopg defaults, so statements are
    prepared after five runs) work through PgBouncer: twelve alternating tenant
    transactions each see only their own workspace."""
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    ws_a, ws_b = make_workspace(db, "A"), make_workspace(db, "B")
    url = pgbouncer.libpq(APP, db.name).replace("postgresql://", "postgresql+psycopg://", 1)
    core_db.configure(app_url=url, pooled=True)
    probe = text("SELECT app.current_workspace_id(), array_agg(id) FROM workspaces")
    try:
        for n in range(12):
            ws = ws_a if n % 2 == 0 else ws_b
            async with tenant_session(WorkspaceContext(ws, SYSTEM_ACTOR)) as s:
                assert tuple((await s.execute(probe)).one()) == (ws, [ws])
    finally:
        await core_db.dispose()
