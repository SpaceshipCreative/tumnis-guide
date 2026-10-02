"""Subprocess entry for the `worker_killer` fixture (P0-07, ADR-0002).

    python -m tumnis.testing.run_worker [--import MODULE]... [--app-version VERSION]
        [--queues A,B] [--log-level LEVEL]

Imports the named modules first (the test subscribers register on import), then runs
`tumnis.worker.main` with deployment settings from the environment, as `tumnis worker`
does but without the boot checks (the harness database carries a `dev` marker already)
and with a pinned DBOS application version, so a restarted worker recovers the pending
workflows of the one that was killed.
"""

import argparse
import faulthandler
import importlib
import signal
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbos import DBOSConfig


def _dump_tasks(_signum: int, _frame: object) -> None:
    """Every asyncio task not yet done, on any loop, with its coroutine's stack (stderr)."""
    import asyncio  # noqa: PLC0415

    # Python 3.13 keeps every loop's tasks in this WeakSet; there is no public all-loops view.
    # DBOS's loop may change it during the copy (RuntimeError), so retry as asyncio's own
    # all_tasks does; a diagnostic must never take the worker down.
    tasks: list[asyncio.Task[object]] = []
    for _ in range(1000):
        try:
            tasks = list(getattr(asyncio.tasks, "_scheduled_tasks", ()))
            break
        except RuntimeError:
            continue
    print(f"--- {len(tasks)} asyncio tasks", file=sys.stderr, flush=True)
    for task in tasks:
        try:
            if not task.done():
                task.print_stack(limit=30, file=sys.stderr)
        except Exception as exc:  # a task finishing meanwhile: note it, keep going
            print(f"--- {task!r}: {exc!r}", file=sys.stderr)
    sys.stderr.flush()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tumnis.testing.run_worker")
    parser.add_argument("--import", dest="imports", action="append", default=[])
    parser.add_argument("--app-version", default=None)
    parser.add_argument("--queues", default=None, help="comma-separated: dequeue only these")
    parser.add_argument("--log-level", default=None, help="DBOS's log level (DBOSConfig)")
    args = parser.parse_args(argv)
    # SIGUSR1 prints every thread's stack, SIGUSR2 every pending asyncio task's (DBOS runs
    # workflows on its own loop): the harness asks for both before it gives up on a worker,
    # so a stalled drain shows where each thread and coroutine waits.
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    signal.signal(signal.SIGUSR2, _dump_tasks)
    for name in args.imports:
        importlib.import_module(name)

    from tumnis import worker  # noqa: PLC0415
    from tumnis.settings import Settings  # noqa: PLC0415

    if args.log_level:
        base = worker.dbos_config

        def with_log_level(settings: Settings) -> "DBOSConfig":
            config = base(settings)
            config["log_level"] = args.log_level
            return config

        worker.dbos_config = with_log_level  # the harness's worker only

    queues = tuple(args.queues.split(",")) if args.queues else None
    worker.main(Settings(), app_version=args.app_version, queues=queues)


if __name__ == "__main__":
    main()
