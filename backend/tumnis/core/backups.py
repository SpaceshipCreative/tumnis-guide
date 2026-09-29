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


def backup_findings(f: BackupFacts) -> list[str]:
    """Findings such as ["wal_stale", "repo2_diff_missing"]; [] when healthy."""
    raise NotImplementedError
