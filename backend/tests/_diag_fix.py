# ruff: noqa: S108
"""TEMPORARY (ci-health): time every fixture's setup and finalizers per xdist worker; each
worker writes $DIAG_FIX_DIR/<worker>.json at session end. Loaded only with -p."""

from __future__ import annotations

import collections
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

SETUP: collections.defaultdict[str, list[float]] = collections.defaultdict(list)
TEARDOWN: collections.defaultdict[str, list[float]] = collections.defaultdict(list)


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef: Any, request: Any) -> Iterator[None]:
    start = time.perf_counter()
    yield
    SETUP[fixturedef.argname].append(time.perf_counter() - start)


def _wrap_finish() -> None:
    from _pytest.fixtures import FixtureDef  # noqa: PLC0415

    original = FixtureDef.finish

    def finish(self: Any, request: Any) -> None:
        start = time.perf_counter()
        try:
            original(self, request)
        finally:
            TEARDOWN[self.argname].append(time.perf_counter() - start)

    FixtureDef.finish = finish  # type: ignore[method-assign]


_wrap_finish()


def pytest_sessionfinish(session: pytest.Session) -> None:
    out = Path(os.environ.get("DIAG_FIX_DIR", "/tmp/diag-fix"))
    out.mkdir(parents=True, exist_ok=True)
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    (out / f"{worker}.json").write_text(json.dumps({"setup": SETUP, "teardown": TEARDOWN}))


def summarize(folder: str) -> None:
    setup: collections.Counter[str] = collections.Counter()
    teardown: collections.Counter[str] = collections.Counter()
    count: collections.Counter[str] = collections.Counter()
    for path in Path(folder).glob("*.json"):
        data = json.loads(path.read_text())
        for name, xs in data["setup"].items():
            setup[name] += sum(xs)
            count[name] += len(xs)
        for name, xs in data["teardown"].items():
            teardown[name] += sum(xs)
    print(
        f"FIX total setup {sum(setup.values()):.0f}s teardown(incl) {sum(teardown.values()):.0f}s"
    )
    for name, t in setup.most_common(40):
        print(f"FIX setup {t:8.1f}s n={count[name]:5d} mean={t / count[name]:.3f}s {name}")
    for name, t in teardown.most_common(25):
        print(f"FIX teardown(incl) {t:8.1f}s {name}")


if __name__ == "__main__":
    import sys

    summarize(sys.argv[1])
