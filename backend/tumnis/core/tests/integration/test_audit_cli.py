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
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
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
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
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
