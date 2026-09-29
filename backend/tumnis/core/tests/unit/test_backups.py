"""The backup freshness rule (P0-28, REL-1): pure, time passed in."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)

FRESH: Mapping[tuple[int, str], datetime] = {
    (1, "full"): NOW - timedelta(days=2),
    (1, "diff"): NOW - timedelta(hours=3),
    (1, "incr"): NOW - timedelta(hours=1),
    (2, "full"): NOW - timedelta(days=2),
    (2, "diff"): NOW - timedelta(hours=3),
}

CASES = [
    # (id, wal_last_archived_at, wal_failed_since_last_success, last_ok, expected)
    ("all_fresh", NOW - timedelta(minutes=1), False, FRESH, []),
    ("wal_6_min_old", NOW - timedelta(minutes=6), False, FRESH, ["wal_stale"]),
    ("wal_never_archived", None, False, FRESH, ["wal_stale"]),
    ("wal_failing", NOW - timedelta(minutes=1), True, FRESH, ["wal_failing"]),
    (
        "repo2_diff_27h_old",
        NOW - timedelta(minutes=1),
        False,
        {**FRESH, (2, "diff"): NOW - timedelta(hours=27)},
        ["repo2_diff_missing"],
    ),
    (
        "repo1_full_9_days_old",
        NOW - timedelta(minutes=1),
        False,
        {**FRESH, (1, "full"): NOW - timedelta(days=9)},
        ["repo1_full_missing"],
    ),
    (
        "sunday_full_counts_as_the_daily",
        NOW - timedelta(minutes=1),
        False,
        {**FRESH, (2, "diff"): NOW - timedelta(days=3), (2, "full"): NOW - timedelta(hours=5)},
        [],
    ),
    (
        "nothing_ever_backed_up",
        NOW - timedelta(minutes=1),
        False,
        {},
        ["repo1_full_missing", "repo1_diff_missing", "repo2_full_missing", "repo2_diff_missing"],
    ),
]


@pytest.mark.req("REL-1")
@pytest.mark.wp("P0-28")
@pytest.mark.xfail(strict=True, reason="spec:P0-28")
@pytest.mark.parametrize(
    ("wal_at", "wal_failing", "last_ok", "expected"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_backup_findings_table(
    wal_at: datetime | None,
    wal_failing: bool,
    last_ok: Mapping[tuple[int, str], datetime],
    expected: list[str],
) -> None:
    """T-P0-28-08
    WAL older than 5 minutes is stale; archive failures since the last success are
    failing; each repository needs a daily (diff, or the weekly full) within 26 hours and a
    full within 8 days. Healthy facts give no finding.
    """
    from tumnis.core.backups import BackupFacts, backup_findings  # noqa: PLC0415

    facts = BackupFacts(
        now=NOW,
        wal_last_archived_at=wal_at,
        wal_failed_since_last_success=wal_failing,
        last_ok=last_ok,
    )
    assert backup_findings(facts) == expected
