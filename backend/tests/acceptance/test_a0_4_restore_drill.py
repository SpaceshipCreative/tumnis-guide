"""A0.4 · Restore drill (phase 0 acceptance, committed red by P0-05).

Runs only in .github/workflows/restore-drill.yml, after scripts/drill/restore_drill.sh
(`pytest -m drill`); every other pytest run deselects `drill` tests. The script does the
restore and its checks and records one `drill.completed` audit row on the source; this
test reads that row and re-checks the thresholds, so they are versioned in code.

Environment (set by the drill workflow): DATABASE_DIRECT_URL (the source database),
DRILL_MODE (prod or rehearsal) and DRILL_STARTED_AT (ISO 8601 UTC, the workflow's start).

Turns green with P0-28.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pytest

pytestmark = [
    pytest.mark.drill,
    pytest.mark.enable_socket,
    pytest.mark.req("REL-1"),
    pytest.mark.wp("P0-05"),
    pytest.mark.xfail(strict=True, reason="spec:P0-05"),
]

RPO_LIMIT_S = 15 * 60  # REL-1: 15-minute RPO
RTO_LIMIT_S = 3_600  # REL-1: 1-hour RTO


def _libpq(url: str) -> str:
    """postgresql+psycopg://... (the settings form) to a libpq URL."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def drill_rows() -> list[dict[str, Any]]:
    """`drill.completed` audit rows written on the source since this drill started."""
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    source = _libpq(os.environ["DATABASE_DIRECT_URL"])
    started = datetime.fromisoformat(os.environ["DRILL_STARTED_AT"])
    with psycopg.connect(source, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT occurred_at, details FROM audit_log "
            "WHERE action = 'drill.completed' AND occurred_at >= %s "
            "AND details->>'mode' = %s ORDER BY occurred_at",
            (started, os.environ["DRILL_MODE"]),
        ).fetchall()


def test_point_in_time_restore_meets_rpo_and_rto() -> None:
    """T-P0-05-04
    Given the drill script finished (marker row present and fence row absent in the
    restored database, migrations at head, audit chain verified), when the recorded
    `drill.completed` row is read from the source, then there is exactly one for this
    drill, its RPO is under 15 minutes and its restore-through-checks time under 1 hour.
    """
    rows = drill_rows()
    assert len(rows) == 1, f"expected one drill.completed row for this drill, got {len(rows)}"
    details = rows[0]["details"]
    assert details["target"], "the drill recorded no recovery target time"
    assert 0 <= float(details["rpo_seconds"]) < RPO_LIMIT_S
    assert 0 < float(details["rto_seconds"]) < RTO_LIMIT_S
