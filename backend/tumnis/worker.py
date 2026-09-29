"""DBOS worker: launch, queue registration, relay, schedules.

The worker and DBOS use the direct database URL, never PgBouncer (LISTEN/NOTIFY and
session-level state do not survive transaction pooling).
"""

import signal
import threading
from typing import TYPE_CHECKING

from tumnis.core import cache, workflows_ops
from tumnis.core.clock import SystemClock
from tumnis.settings import Settings, install_master_keys

if TYPE_CHECKING:
    from dbos import DBOSConfig


def register_queues() -> None:
    """Register every DBOS queue (A9). DBOS 3.1 persists queues in the system database, so
    this runs right after DBOS.launch(), in the worker and in the test harness alike.
    P0-07 adds `events`."""
    from dbos import DBOS  # noqa: PLC0415

    DBOS.register_queue(workflows_ops.MAINTENANCE_QUEUE, worker_concurrency=1)


def register_schedules(settings: Settings) -> None:
    """Static schedules (A9), applied after DBOS.launch(). The backup freshness check runs
    only in production: previews and dev stacks keep no backups to check."""
    from dbos import DBOS  # noqa: PLC0415

    if settings.deployment_env == "prod":
        DBOS.apply_schedules(
            [
                {
                    "schedule_name": "backup-freshness",
                    "workflow_fn": workflows_ops.backup_freshness_check,
                    "schedule": workflows_ops.BACKUP_FRESHNESS_SCHEDULE,
                    "queue_name": workflows_ops.MAINTENANCE_QUEUE,
                }
            ]
        )


def dbos_config(settings: Settings) -> "DBOSConfig":
    return {"name": "tumnis", "system_database_url": settings.dbos_system_url}


def main(settings: Settings) -> None:
    """Launch DBOS and block until SIGTERM or SIGINT."""
    from dbos import DBOS  # noqa: PLC0415

    import tumnis.wiring  # noqa: F401, PLC0415  # registers adapters (later: workflows)
    from tumnis.core import db  # noqa: PLC0415

    db.configure(settings.database_direct_url, settings.database_direct_url)
    install_master_keys(settings)
    cache.configure_backend(
        cache.InProcessCache(SystemClock(), publish=cache.pg_publisher(db.direct_engine))
    )
    stop_listener = cache.CacheInvalidationListener(settings.database_direct_url).start_thread()
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    DBOS(config=dbos_config(settings))
    DBOS.launch()
    register_queues()
    register_schedules(settings)
    try:
        stop.wait()
    finally:
        stop_listener()
        DBOS.destroy()
