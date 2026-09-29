"""Operations workflows (P0-28): the `*/15` backup freshness check on the maintenance queue."""

from datetime import datetime
from typing import Any, Protocol

from tumnis.core.backups import BackupFacts


class BackupFactsSource(Protocol):
    async def read(self, now: datetime) -> BackupFacts: ...


def set_backup_facts_source(source: BackupFactsSource | None) -> BackupFactsSource | None:
    """Swap the facts source (tests script a fake); returns the previous one."""
    raise NotImplementedError


async def backup_freshness_check(scheduled_at: datetime, context: Any) -> None:
    raise NotImplementedError
