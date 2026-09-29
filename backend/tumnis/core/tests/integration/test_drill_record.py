"""`tumnis drill record` (P0-28, REL-1, SEC-3): the drill's numbers land in the audit log
and in ops_status."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("REL-1", "SEC-3")
@pytest.mark.wp("P0-28")
def test_drill_record_writes_audit_row(
    db: DbUrls, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P0-28-10
    `tumnis drill record --mode rehearsal --rpo-seconds 75 --rto-seconds 420 --target T`
    writes one audit_log row with action drill.completed whose details carry both numbers,
    and upserts ops_status check restore_drill (ok, checked at the clock's time). The row
    goes to the deployment's first workspace, so the test creates one (issue #10).
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis import cli  # noqa: PLC0415

    make_workspace(db, "Drill")
    monkeypatch.setattr(cli, "make_clock", lambda: clock)
    env = {
        "DATABASE_URL": db.app,
        "DATABASE_DIRECT_URL": db.app,
        "DEPLOYMENT_ENV": "dev",
        "TUMNIS_ADAPTERS": "fake",
    }
    target = "2026-03-09T11:58:00+00:00"
    args = ["drill", "record", "--mode", "rehearsal", "--rpo-seconds", "75"]
    args += ["--rto-seconds", "420", "--target", target]
    result = CliRunner().invoke(cli.app, args, env=env)
    assert result.exit_code == 0, result.output

    with psycopg.connect(db.libpq(OWNER)) as conn:
        audit = conn.execute(
            "SELECT details FROM audit_log WHERE action = 'drill.completed'"
        ).fetchall()
        status = conn.execute(
            "SELECT ok, checked_at, details FROM ops_status WHERE \"check\" = 'restore_drill'"
        ).fetchall()
    assert len(audit) == 1
    details = audit[0][0]
    assert details["rpo_seconds"] == 75
    assert details["rto_seconds"] == 420
    assert details["mode"] == "rehearsal"
    assert len(status) == 1
    ok, checked_at, status_details = status[0]
    assert ok is True
    assert checked_at == clock.now()
    assert status_details["rpo_seconds"] == 75
    assert status_details["rto_seconds"] == 420


def _record(db: DbUrls, *args: str) -> tuple[int, str]:
    from typer.testing import CliRunner  # noqa: PLC0415

    from tumnis import cli  # noqa: PLC0415

    env = {"DATABASE_URL": db.app, "DATABASE_DIRECT_URL": db.app, "DEPLOYMENT_ENV": "dev"}
    result = CliRunner().invoke(cli.app, ["drill", "record", *args], env=env)
    return result.exit_code, result.output


@pytest.mark.req("REL-1")
@pytest.mark.wp("P0-28")
def test_drill_record_upserts_ops_status_and_fails_over_the_limits(
    db: DbUrls, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tumnis drill record` keeps one ops_status row for restore_drill with both numbers
    and the limits; an RTO over an hour is recorded as not ok and exits 1."""
    from tumnis import cli  # noqa: PLC0415

    monkeypatch.setattr(cli, "make_clock", lambda: clock)
    target = "2026-03-09 11:58:00.123456+00"  # clock_timestamp() as psql prints it
    code, output = _record(
        db, "--mode", "prod", "--rpo-seconds", "80", "--rto-seconds", "900", "--target", target
    )
    assert code == 0, output
    code, output = _record(
        db, "--mode", "prod", "--rpo-seconds", "80", "--rto-seconds", "3601", "--target", target
    )
    assert code == 1, output
    assert "MISSED" in output

    with psycopg.connect(db.libpq(OWNER)) as conn:
        rows = conn.execute(
            "SELECT ok, details FROM ops_status WHERE \"check\" = 'restore_drill'"
        ).fetchall()
    assert len(rows) == 1
    ok, details = rows[0]
    assert ok is False
    assert details["rto_seconds"] == 3601
    assert details["rto_limit_seconds"] == 3600
    assert details["rpo_limit_seconds"] == 900
    assert details["target"] == "2026-03-09T11:58:00.123456+00:00"


@pytest.mark.req("REL-1")
@pytest.mark.wp("P0-28")
def test_drill_record_refuses_a_naive_target(db: DbUrls) -> None:
    """The target needs a UTC offset: a naive time is a usage error, nothing recorded."""
    code, _ = _record(
        db,
        "--mode",
        "rehearsal",
        "--rpo-seconds",
        "1",
        "--rto-seconds",
        "1",
        "--target",
        "2026-03-09 11:58:00",
    )
    assert code == 2
