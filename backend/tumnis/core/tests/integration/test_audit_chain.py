"""The per-workspace hash chain and its anchors (P0-15, SEC-3): edits and deletions made
straight in the database, as the owner, are found by `verify_chain`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _verify(workspace: WorkspaceHandle) -> list[tuple[int, str]]:
    from tumnis.core import audit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    async with tenant_session(workspace.ctx) as s:
        breaks = await audit.verify_chain(s, workspace.id)
    assert all(b.workspace_id == workspace.id for b in breaks)
    return [(b.seq, b.kind) for b in breaks]


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
async def test_verify_catches_an_edited_row(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-15-03
    Ten rows verify clean; the owner disables the trigger and changes `details` of seq 5:
    `verify_chain` returns one `hash_mismatch` at 5.
    """
    from tumnis.core.tests.integration._audit import configured, tamper, write_rows  # noqa: PLC0415

    async with configured(db):
        await write_rows(workspace.ctx, 10, clock)
        assert await _verify(workspace) == []
        changed = tamper(
            db,
            """UPDATE audit_log SET details = '{"n": 50}' WHERE workspace_id = %s AND seq = 5""",
            (workspace.id,),
        )
        assert changed == 1
        assert await _verify(workspace) == [(5, "hash_mismatch")]


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
async def test_verify_catches_a_deleted_row(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-15-04
    The owner deletes seq 4 of 10: `verify_chain` returns one `gap` at 4.
    """
    from tumnis.core.tests.integration._audit import configured, tamper, write_rows  # noqa: PLC0415

    async with configured(db):
        await write_rows(workspace.ctx, 10, clock)
        deleted = tamper(
            db, "DELETE FROM audit_log WHERE workspace_id = %s AND seq = 4", (workspace.id,)
        )
        assert deleted == 1
        assert await _verify(workspace) == [(4, "gap")]


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
async def test_verify_catches_truncation_after_anchor(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-15-05
    Anchor at seq 10, then the owner deletes 9 and 10: the chain up to 8 is intact, but
    `verify_chain` returns `truncated_after_anchor` (at the anchored seq, 10).
    """
    from tumnis.core import audit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import configured, tamper, write_rows  # noqa: PLC0415

    async with configured(db):
        await write_rows(workspace.ctx, 10, clock)
        async with tenant_session(workspace.ctx) as s:
            await audit.anchor(s, workspace.id, clock.now())
        assert await _verify(workspace) == []
        deleted = tamper(
            db, "DELETE FROM audit_log WHERE workspace_id = %s AND seq >= 9", (workspace.id,)
        )
        assert deleted == 2
        assert await _verify(workspace) == [(10, "truncated_after_anchor")]


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
async def test_concurrent_writers_keep_one_chain(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-15-06
    50 concurrent `record` calls in one workspace, each in its own `tenant_session`:
    seq 1..50 with no gap, and the chain verifies.
    """
    from tumnis.core import audit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import configured, owner_rows  # noqa: PLC0415

    async def one(i: int) -> None:
        async with tenant_session(workspace.ctx) as s:
            await audit.record(s, "test.concurrent", details={"i": i}, occurred_at=clock.now())

    async with configured(db):
        await asyncio.gather(*(one(i) for i in range(50)))
        seqs = owner_rows(
            db, "SELECT seq FROM audit_log WHERE workspace_id = %s ORDER BY seq", (workspace.id,)
        )
        assert [seq for (seq,) in seqs] == list(range(1, 51))
        assert await _verify(workspace) == []
