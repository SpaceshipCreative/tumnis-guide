"""DBOS worker: launch, queue registration, relay, schedules.

The worker and DBOS use the direct database URL, never PgBouncer (LISTEN/NOTIFY and
session-level state do not survive transaction pooling).
"""

import asyncio
import contextlib
import signal
from typing import TYPE_CHECKING

from tumnis.core import audit_workflows, events, faults, workflows_ops
from tumnis.settings import Settings

if TYPE_CHECKING:
    from dbos import DBOSConfig


def register_queues() -> None:
    """Register every DBOS queue (A9). DBOS 3.1 persists queues in the system database, so
    this runs right after DBOS.launch(), in the worker and in the test harness alike."""
    from dbos import DBOS  # noqa: PLC0415

    DBOS.register_queue(
        events.EVENTS_QUEUE,
        worker_concurrency=events.EVENTS_WORKER_CONCURRENCY,
        polling_interval_sec=events.EVENTS_QUEUE_POLL_S,
    )
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


def register_audit_schedule() -> None:
    """The nightly audit chain verify (P0-15), in every deployment: 03:23 UTC on the
    maintenance queue, applied after DBOS.launch()."""
    from dbos import DBOS  # noqa: PLC0415

    DBOS.apply_schedules(
        [
            {
                "schedule_name": audit_workflows.SCHEDULE_NAME,
                "workflow_fn": audit_workflows.audit_verify,
                "schedule": audit_workflows.AUDIT_VERIFY_SCHEDULE,
                "queue_name": workflows_ops.MAINTENANCE_QUEUE,
            }
        ]
    )


def dbos_config(settings: Settings) -> "DBOSConfig":
    return {"name": "tumnis", "system_database_url": settings.dbos_system_url}


def main(settings: Settings, *, app_version: str | None = None) -> None:
    """Launch DBOS, register queues and schedules, run the outbox relay beside it, and block
    until SIGTERM or SIGINT. A kill point (TUMNIS_KILLPOINT, tests only) is armed first, and
    refused in production before anything connects. `app_version` pins DBOS's application
    version (the kill-and-resume harness runs two workers that must share it)."""
    faults.arm(settings.deployment_env)

    from dbos import DBOS  # noqa: PLC0415

    import tumnis.wiring  # noqa: F401, PLC0415  # registers adapters (later: workflows)
    from tumnis.core import db  # noqa: PLC0415

    db.configure(settings.database_direct_url, settings.database_direct_url)
    config = dbos_config(settings)
    if app_version is not None:
        config["application_version"] = app_version
    DBOS(config=config)
    DBOS.launch()
    register_queues()
    register_schedules(settings)
    register_audit_schedule()
    try:
        asyncio.run(_serve())
    finally:
        DBOS.destroy()


async def _serve() -> None:
    """The relay on this thread's event loop (DBOS runs workflows on its own) until a
    signal; then the relay is cancelled, not waited for (it may sit in a LISTEN wait)."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    relay = asyncio.create_task(events.relay_forever(stop), name="outbox-relay")
    await stop.wait()
    relay.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await relay
