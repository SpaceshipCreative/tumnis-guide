"""DBOS worker: launch, queue registration, relay, schedules.

The worker and DBOS use the direct database URL, never PgBouncer (LISTEN/NOTIFY and
session-level state do not survive transaction pooling).
"""

import asyncio
import contextlib
import importlib
import signal
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from tumnis.core import audit_workflows, cache, events, faults, modules, workflows_ops
from tumnis.core.clock import SystemClock
from tumnis.settings import Settings, install_master_keys, install_peppers

if TYPE_CHECKING:
    from dbos import DBOSConfig

SYNC_QUEUE = "sync"  # connector syncs and OAuth exchanges (A9; P1-09)
SYNC_WORKER_CONCURRENCY = 4  # plan default
GITHUB_QUEUE = "github"  # pull request status reads (P2-13); its own queue: limiters are per queue
GITHUB_REFRESHES_PER_MINUTE = 15  # each refresh makes four requests, 304s included
FOCUS_QUEUE = "focus"  # focus_plan and focus_session (P2-15); each parks on its next instant
EXTRACT_QUEUE = "extract"  # upload scanning and extraction (P1-16); only `worker-extract` listens
EMBED_QUEUE = "embed"  # re-embedding on a model change (P3-10; knowledge.rules.EMBED_QUEUE)
EMBED_WORKER_CONCURRENCY = 2  # plan default


def _agents() -> Any:
    """agents.workflows, imported by name (as wiring does) so no module's tests import
    another module through this composition root."""
    return importlib.import_module("tumnis.modules.agents.workflows")


def _projects() -> Any:
    """projects.workflows, imported by name (see `_agents`)."""
    return importlib.import_module("tumnis.modules.projects.workflows")


def main_queues() -> list[str]:
    """Every queue register_queues registers except `extract`: what the main worker listens to."""
    agents = _agents()
    return [
        events.EVENTS_QUEUE,
        workflows_ops.MAINTENANCE_QUEUE,
        SYNC_QUEUE,
        agents.RUNS_QUEUE,
        agents.HUMAN_QUEUE,
        agents.RUNNER_SWEEP_QUEUE,
        GITHUB_QUEUE,
        _projects().ARCHIVE_QUEUE,
        FOCUS_QUEUE,
        EMBED_QUEUE,
    ]


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
    DBOS.register_queue(SYNC_QUEUE, worker_concurrency=SYNC_WORKER_CONCURRENCY)
    # Agent runs and profile health checks (P1-04), partitioned by profile.
    agents = _agents()
    # dispatch_run (P2-04) is partitioned by project, two at a time per project (SAF-5).
    DBOS.register_queue(
        agents.RUNS_QUEUE,
        partition_concurrency=agents.RUNS_PARTITION_CONCURRENCY,
        polling_interval_sec=agents.RUNS_QUEUE_POLL_S,
    )
    DBOS.register_queue(agents.RUNNER_SWEEP_QUEUE, worker_concurrency=1)
    # Questions and approvals (P2-05): no limit, each flow parks on the human.
    DBOS.register_queue(agents.HUMAN_QUEUE)
    DBOS.register_queue(
        GITHUB_QUEUE,
        worker_concurrency=2,
        limiter={"limit": GITHUB_REFRESHES_PER_MINUTE, "period": 60},
    )
    DBOS.register_queue(EXTRACT_QUEUE, worker_concurrency=1)  # one heavy conversion at a time
    # Project archive, unarchive and purge (P2-18): one at a time across the deployment.
    # Not partitioned: DBOS then requires a partition key on every enqueue.
    projects = _projects()
    DBOS.register_queue(projects.ARCHIVE_QUEUE, concurrency=projects.ARCHIVE_CONCURRENCY)
    # Focus workflows (P2-15): no limit, each waits on `recv` for its next instant.
    DBOS.register_queue(FOCUS_QUEUE)
    # Re-embedding (P3-10): two batches at a time, so a build never starves the embedder.
    DBOS.register_queue(EMBED_QUEUE, worker_concurrency=EMBED_WORKER_CONCURRENCY)


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


def register_runner_sweep() -> None:
    """The runner sweep (P1-04), each minute on its own queue (never behind a long
    maintenance job), in every deployment: runners offline after three missed heartbeats,
    their runs `runner_lost`."""
    from dbos import DBOS  # noqa: PLC0415

    agents = _agents()
    DBOS.apply_schedules(
        [
            {
                "schedule_name": agents.RUNNER_SWEEP_NAME,
                "workflow_fn": agents.runner_sweep,
                "schedule": agents.RUNNER_SWEEP_SCHEDULE,
                "queue_name": agents.RUNNER_SWEEP_QUEUE,
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


def configure_generation(settings: Settings) -> None:
    """The Generation slot's endpoint and timeouts (P1-03): only the worker asks it. The
    vLLM decisions fallback (P1-02) gets the same SSRF policy."""
    decisions = importlib.import_module("tumnis.modules.decisions.api")
    decisions.configure_generation(settings.generation, net_policy=settings.net_policy())
    decisions.configure_net_policy(settings.net_policy())
    # The Embeddings slot (P3-10): only the worker embeds chunks and search queries; the
    # api process never calls out, so its hybrid searches answer with full text.
    decisions.configure_embeddings(settings.embeddings, net_policy=settings.net_policy())
    # The Speech slot (P4-03): only the worker makes spoken focus messages' clips.
    decisions.configure_speech(settings.speech, net_policy=settings.net_policy())


def configure_agents(settings: Settings) -> None:
    """How long profile provisioning waits for the runner (P1-06), and a run's time caps
    (P2-04, R-30; shortened only with fakes, else the plan defaults): only the worker runs
    `provision_profile` and `dispatch_run`."""
    agents = importlib.import_module("tumnis.modules.agents.api")
    agents.configure_provisioning(timeout_s=settings.agents.provision_timeout_s)
    agents.configure_runs(
        active_cap_seconds=settings.agents.run_active_cap_seconds,
        wall_clock_ceiling_seconds=settings.agents.run_wall_clock_ceiling_seconds,
    )
    agents.configure_stuck(deadline_seconds=settings.agents.stuck_deadline_seconds)


def configure_folder_sync(settings: Settings) -> None:
    """The folder sync's SSRF policy (P1-15): every location is opened with the worker's."""
    importlib.import_module("tumnis.modules.knowledge.sync").configure(settings.net_policy())


def _folder_watch(stop: asyncio.Event) -> "asyncio.Task[None]":
    """The local-disk folder watcher (P1-15) beside the relay: changes queue a folder sync."""
    knowledge = importlib.import_module("tumnis.modules.knowledge.workflows")
    return asyncio.create_task(knowledge.local_watch(stop), name="folder-watch")


def register_module_schedules() -> None:
    """Module schedules (A9), applied after DBOS.launch(): a module's `workflows.schedules()`
    lists its own (P1-09: the calendar sync tick every 10 minutes on the sync queue)."""
    from dbos import DBOS  # noqa: PLC0415

    found = []
    for module in modules.MODULES:
        workflows = importlib.import_module(f"tumnis.modules.{module}.workflows")
        declared = getattr(workflows, "schedules", None)
        if declared is not None:
            found += declared()
    if found:
        DBOS.apply_schedules(found)


def register_task_schedules() -> None:
    """The day-close tick (every 5 minutes, also the recurrence tick) and hourly
    housekeeping, both on the maintenance queue (P0-19; the tick since P1-16), in every
    deployment, applied after DBOS.launch(); applying again replaces them by name, so a
    restart adds no duplicates."""
    from dbos import DBOS  # noqa: PLC0415

    # Imported by name, as wiring does: the composition root reaches module workflows
    # without a static edge (import-linter's modules-api-only sees static imports only).
    tasks = importlib.import_module("tumnis.modules.tasks.workflows")
    DBOS.apply_schedules(
        [
            {
                "schedule_name": tasks.DAY_CLOSE_SCHEDULE_NAME,
                "workflow_fn": tasks.day_close_tick,
                "schedule": tasks.DAY_CLOSE_SCHEDULE,
                # Named, not DBOS's internal queue: every process services that one whatever
                # listen_queues says, so the extract worker could otherwise take the tick.
                "queue_name": workflows_ops.MAINTENANCE_QUEUE,
            },
            {
                "schedule_name": tasks.HOUSEKEEPING_SCHEDULE_NAME,
                "workflow_fn": tasks.housekeeping,
                "schedule": tasks.HOUSEKEEPING_SCHEDULE,
                "queue_name": workflows_ops.MAINTENANCE_QUEUE,
            },
        ]
    )


def configure_extraction(settings: Settings) -> None:
    """The extraction pipeline's folders, clamd address and SSRF policy (P1-16): only the
    worker runs its steps."""
    pipeline = importlib.import_module("tumnis.modules.knowledge.pipeline")
    pipeline.configure(settings.knowledge, net=settings.net_policy())


def dbos_config(settings: Settings) -> "DBOSConfig":
    return {"name": "tumnis", "system_database_url": settings.dbos_system_url}


def main(
    settings: Settings, *, app_version: str | None = None, queues: Sequence[str] | None = None
) -> None:
    """Launch DBOS, register queues and schedules, run the outbox relay beside it, and block
    until SIGTERM or SIGINT. A kill point (TUMNIS_KILLPOINT, tests only) is armed first, and
    refused in production before anything connects. `app_version` pins DBOS's application
    version (the kill-and-resume harness runs two workers that must share it).

    Without `queues` the worker listens to every queue but `extract` and also runs the
    relay and the schedules. With `queues` (`tumnis worker --queues extract`) it dequeues
    only those, runs neither, and names its DBOS executor `worker-<queues>`."""
    faults.arm(settings.deployment_env)

    from dbos import DBOS  # noqa: PLC0415

    import tumnis.wiring  # noqa: F401, PLC0415  # registers adapters, events and workflows
    from tumnis.core import db, fake_scripts  # noqa: PLC0415

    db.configure(settings.database_direct_url, settings.database_direct_url)
    if settings.tumnis_adapters == "fake":
        fake_scripts.enable()  # scripts posted to the api reach this process's fakes (R-37)
    install_master_keys(settings)
    # dispatch issues each run's task token (P2-02, R-27): its HMAC needs the peppers here
    # too, not only in the api (the boot checks install them; a harness worker skips those).
    install_peppers(settings)
    modules.configure(settings)
    configure_generation(settings)
    configure_agents(settings)
    configure_folder_sync(settings)
    configure_extraction(settings)
    cache.configure_backend(
        cache.InProcessCache(SystemClock(), publish=cache.pg_publisher(db.direct_engine))
    )
    config = dbos_config(settings)
    if app_version is not None:
        config["application_version"] = app_version
    if queues:
        config["executor_id"] = "worker-" + "-".join(queues)
    DBOS(config=config)
    DBOS.listen_queues(list(queues) if queues else main_queues())
    DBOS.launch()
    register_queues()
    if not queues:
        register_schedules(settings)
        register_audit_schedule()
        register_module_schedules()
        register_runner_sweep()
        register_task_schedules()
    try:
        asyncio.run(_serve(settings, relay=not queues))
    finally:
        DBOS.destroy()


async def _serve(settings: Settings, *, relay: bool = True) -> None:
    """The relay and the folder watcher (both skipped when `relay` is off: a queue-limited
    worker) and the cache invalidation listener (P0-08) on this thread's event loop (DBOS
    runs workflows on its own) until a signal; then all are cancelled, not waited for (any
    may sit in a LISTEN wait)."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    # The relay and the folder watcher (P1-15) run only in the main worker.
    background = (
        [
            asyncio.create_task(events.relay_forever(stop), name="outbox-relay"),
            _folder_watch(stop),
        ]
        if relay
        else []
    )
    listener = cache.CacheInvalidationListener(settings.database_direct_url)
    invalidations = asyncio.create_task(listener.run(stop), name="cache-invalidation")
    await stop.wait()
    for task in (*background, invalidations):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
