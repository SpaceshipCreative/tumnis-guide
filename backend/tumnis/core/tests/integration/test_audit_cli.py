"""`tumnis audit verify` and the nightly `audit_verify` workflow (P0-15, SEC-3, REL-5)."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import uuid

    from dbos import DBOS

    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

BREAK_SEQ2 = """UPDATE audit_log SET details = '{"n": 20}' WHERE workspace_id = %s AND seq = 2"""


def _write(db: DbUrls, clock: FixedClock, *workspaces: uuid.UUID, n: int = 3) -> None:
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import configured, write_rows  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    async def run() -> None:
        async with configured(db):
            for workspace in workspaces:
                await write_rows(WorkspaceContext(workspace, SYSTEM_ACTOR), n, clock)

    asyncio.run(run())


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
def test_audit_verify_cli_exit_codes(db: DbUrls, clock: FixedClock) -> None:
    """T-P0-15-13
    `tumnis audit verify` exits 0 when every workspace's chain is clean; after the owner
    edits seq 2 in B it exits 1 with a report line naming B, seq 2 and `hash_mismatch`
    (and nothing about A); `--workspace A` still exits 0.
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis import cli  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import tamper  # noqa: PLC0415

    a, b = make_workspace(db, "A"), make_workspace(db, "B")
    _write(db, clock, a, b)
    env = {"DATABASE_URL": db.app, "DATABASE_DIRECT_URL": db.app, "DEPLOYMENT_ENV": "dev"}

    clean = CliRunner().invoke(cli.app, ["audit", "verify"], env=env)
    assert clean.exit_code == 0, clean.output

    assert tamper(db, BREAK_SEQ2, (b,)) == 1
    broken = CliRunner().invoke(cli.app, ["audit", "verify"], env=env)
    assert broken.exit_code == 1, broken.output
    lines = [line for line in broken.output.splitlines() if str(b) in line]
    assert any(re.search(r"\bseq\D*2\b", line) and "hash_mismatch" in line for line in lines), (
        broken.output
    )
    assert str(a) not in broken.output

    only_a = CliRunner().invoke(cli.app, ["audit", "verify", "--workspace", str(a)], env=env)
    assert only_a.exit_code == 0, only_a.output


@pytest.mark.req("SEC-3", "REL-5")
@pytest.mark.wp("P0-15")
async def test_nightly_verify_sets_metric_and_anchors(
    dbos: type[DBOS], db: DbUrls, clock: FixedClock
) -> None:
    """T-P0-15-14
    One run of `audit_verify`: the clean workspace A gets an anchor at its head (seq and
    hash) and `tumnis_audit_chain_ok{workspace=A}` = 1; B, edited at seq 2, gets 0 and no
    anchor.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.audit_workflows import audit_verify  # noqa: PLC0415
    from tumnis.core.metrics import REGISTRY  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import owner_rows, tamper, write_rows  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    a, b = make_workspace(db, "A"), make_workspace(db, "B")
    for workspace in (a, b):
        await write_rows(WorkspaceContext(workspace, SYSTEM_ACTOR), 3, clock)
    assert tamper(db, BREAK_SEQ2, (b,)) == 1

    await audit_verify(clock.now(), None)

    head = owner_rows(db, "SELECT hash FROM audit_log WHERE workspace_id = %s AND seq = 3", (a,))
    anchors = owner_rows(db, "SELECT workspace_id, seq, hash, anchored_at FROM audit_anchors")
    assert anchors == [(a, 3, head[0][0], clock.now())]
    assert REGISTRY.get_sample_value("tumnis_audit_chain_ok", {"workspace": str(a)}) == 1
    assert REGISTRY.get_sample_value("tumnis_audit_chain_ok", {"workspace": str(b)}) == 0


@pytest.mark.req("SEC-3", "REL-5")
@pytest.mark.wp("P0-15")
def test_audit_verify_is_scheduled_nightly_on_the_maintenance_queue(dbos: type[DBOS]) -> None:
    """The worker applies `audit-verify` at 03:23 UTC on the maintenance queue in every
    deployment."""
    from tumnis.core import audit_workflows  # noqa: PLC0415
    from tumnis.worker import register_audit_schedule  # noqa: PLC0415

    register_audit_schedule()
    (schedule,) = dbos.list_schedules()
    assert schedule["schedule_name"] == "audit-verify"
    assert schedule["schedule"] == audit_workflows.AUDIT_VERIFY_SCHEDULE == "23 3 * * *"
    assert schedule["queue_name"] == "maintenance"


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
async def test_verify_reports_an_anchor_that_no_longer_matches(
    db: DbUrls, clock: FixedClock
) -> None:
    """An anchored row whose hash was rewritten (and the chain re-hashed after it, as an
    attacker with the owner password could) still differs from its anchor."""
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core import audit  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import configured, tamper, write_rows  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    ws = make_workspace(db)
    ctx = WorkspaceContext(ws, SYSTEM_ACTOR)
    async with configured(db):
        await write_rows(ctx, 2, clock)
        async with tenant_session(ctx) as s:
            await audit.anchor(s, ws, clock.now())
        assert (
            tamper(
                db, "UPDATE audit_log SET hash = %s WHERE workspace_id = %s AND seq = 2", (b"x", ws)
            )
            == 1
        )
        async with tenant_session(ctx) as s:
            breaks = await audit.verify_chain(s, ws)
    assert [(b.seq, b.kind) for b in breaks] == [(2, "hash_mismatch"), (2, "anchor_mismatch")]


@pytest.mark.req("SEC-3", "REL-5")
@pytest.mark.wp("P0-15")
async def test_nightly_verify_records_ops_status(
    dbos: type[DBOS], db: DbUrls, clock: FixedClock
) -> None:
    """The run's outcome reaches the api process through ops_status "audit_chain": not ok,
    naming the broken workspace, while any chain is broken."""
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.audit_workflows import audit_verify  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import owner_rows, tamper, write_rows  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    a, b = make_workspace(db, "A"), make_workspace(db, "B")
    for workspace in (a, b):
        await write_rows(WorkspaceContext(workspace, SYSTEM_ACTOR), 3, clock)
    await audit_verify(clock.now(), None)
    status = 'SELECT ok, checked_at, details FROM ops_status WHERE "check" = %s'
    assert owner_rows(db, status, ("audit_chain",)) == [
        (True, clock.now(), {"checked": 2, "broken": []})
    ]

    assert tamper(db, BREAK_SEQ2, (b,)) == 1
    clock.advance(days=1)
    await audit_verify(clock.now(), None)
    assert owner_rows(db, status, ("audit_chain",)) == [
        (False, clock.now(), {"checked": 2, "broken": [str(b)]})
    ]


@pytest.mark.req("SEC-3", "REL-1")
@pytest.mark.wp("P0-15")
def test_drill_record_audits_in_the_first_workspace(
    db: DbUrls, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tumnis drill record` writes one `drill.completed` row, with the drill's numbers, in
    the deployment's first workspace, on the chain `tumnis audit verify` accepts."""
    from typer.testing import CliRunner  # noqa: PLC0415

    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis import cli  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import owner_rows  # noqa: PLC0415

    first, _second = make_workspace(db, "First"), make_workspace(db, "Second")
    monkeypatch.setattr(cli, "make_clock", lambda: clock)
    env = {"DATABASE_URL": db.app, "DATABASE_DIRECT_URL": db.app, "DEPLOYMENT_ENV": "dev"}
    args = ["drill", "record", "--mode", "rehearsal", "--rpo-seconds", "75"]
    args += ["--rto-seconds", "420", "--target", "2026-03-09T11:58:00+00:00"]
    recorded = CliRunner().invoke(cli.app, args, env=env)
    assert recorded.exit_code == 0, recorded.output

    rows = owner_rows(
        db,
        "SELECT workspace_id, seq, actor_type, occurred_at, details FROM audit_log "
        "WHERE action = 'drill.completed'",
    )
    assert len(rows) == 1
    workspace_id, seq, actor_type, occurred_at, details = rows[0]
    assert (workspace_id, seq, actor_type, occurred_at) == (first, 1, "system", clock.now())
    assert (details["rpo_seconds"], details["rto_seconds"], details["mode"]) == (
        75,
        420,
        "rehearsal",
    )
    verified = CliRunner().invoke(cli.app, ["audit", "verify"], env=env)
    assert verified.exit_code == 0, verified.output
