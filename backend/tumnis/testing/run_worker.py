"""Subprocess entry for the `worker_killer` fixture (P0-07, ADR-0002).

    python -m tumnis.testing.run_worker [--import MODULE]... [--app-version VERSION] [--queues A,B]

Imports the named modules first (the test subscribers register on import), then runs
`tumnis.worker.main` with deployment settings from the environment, as `tumnis worker`
does but without the boot checks (the harness database carries a `dev` marker already)
and with a pinned DBOS application version, so a restarted worker recovers the pending
workflows of the one that was killed.
"""

import argparse
import importlib
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tumnis.testing.run_worker")
    parser.add_argument("--import", dest="imports", action="append", default=[])
    parser.add_argument("--app-version", default=None)
    parser.add_argument("--queues", default=None, help="comma-separated: dequeue only these")
    args = parser.parse_args(argv)
    for name in args.imports:
        importlib.import_module(name)

    from tumnis.settings import Settings  # noqa: PLC0415
    from tumnis.worker import main as worker_main  # noqa: PLC0415

    queues = tuple(args.queues.split(",")) if args.queues else None
    worker_main(Settings(), app_version=args.app_version, queues=queues)


if __name__ == "__main__":
    main()
