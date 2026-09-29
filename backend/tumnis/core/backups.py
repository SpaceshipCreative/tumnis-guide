"""Backup freshness rule (P0-28, REL-1). Pure: no I/O, the time is an input.

The worker's `backup_freshness_check` workflow (core/workflows_ops.py) gathers the facts
from `pg_stat_archiver` and `ops_backup_runs` and passes them here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final


@dataclass(frozen=True, slots=True)
class BackupFacts:
    now: datetime
    wal_last_archived_at: datetime | None
    wal_failed_since_last_success: bool
    last_ok: Mapping[tuple[int, str], datetime]  # (repo, type) -> finished_at of last success


WAL_MAX_AGE: Final = timedelta(minutes=5)  # (plan default) inside the 15-minute RPO
BACKUP_MAX_AGE: Final = timedelta(hours=26)  # daily diff plus slack (plan default)
FULL_MAX_AGE: Final = timedelta(days=8)  # weekly full plus slack (plan default)


REPOS: Final = (1, 2)
# The daily backup is a diff on weekdays and the full on Sunday: either one satisfies it.
_DAILY_TYPES: Final = ("diff", "full")


def _latest(f: BackupFacts, repo: int, types: tuple[str, ...]) -> datetime | None:
    stamps = [f.last_ok[(repo, t)] for t in types if (repo, t) in f.last_ok]
    return max(stamps, default=None)


def _older_than(f: BackupFacts, at: datetime | None, limit: timedelta) -> bool:
    return at is None or f.now - at > limit


def backup_findings(f: BackupFacts) -> list[str]:
    """Findings such as ["wal_stale", "repo2_diff_missing"]; [] when healthy.

    WAL not archived within WAL_MAX_AGE is `wal_stale`; archive failures since the last
    success are `wal_failing`. Each repository needs a full within FULL_MAX_AGE
    (`repo<n>_full_missing`) and a daily backup, diff or full, within BACKUP_MAX_AGE
    (`repo<n>_diff_missing`).
    """
    findings: list[str] = []
    if _older_than(f, f.wal_last_archived_at, WAL_MAX_AGE):
        findings.append("wal_stale")
    if f.wal_failed_since_last_success:
        findings.append("wal_failing")
    for repo in REPOS:
        if _older_than(f, _latest(f, repo, ("full",)), FULL_MAX_AGE):
            findings.append(f"repo{repo}_full_missing")
        if _older_than(f, _latest(f, repo, _DAILY_TYPES), BACKUP_MAX_AGE):
            findings.append(f"repo{repo}_diff_missing")
    return findings
