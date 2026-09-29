"""TEMPORARY measurement plugin (fix/ci-integration-budget): per-fixture setup time.

Active only when TUMNIS_FIXTURE_TIMING names a folder; each xdist worker writes its totals
there at session end. Removed before the PR is ready.
"""

from __future__ import annotations

import collections
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_OUT = os.environ.get("TUMNIS_FIXTURE_TIMING")
_TOTALS: collections.Counter[str] = collections.Counter()
_COUNTS: collections.Counter[str] = collections.Counter()
_STACK: list[float] = []


if _OUT:

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(fixturedef: Any, request: Any) -> Iterator[None]:
        start = time.perf_counter()
        _STACK.append(0.0)
        yield
        total = time.perf_counter() - start
        inner = _STACK.pop()
        own = total - inner
        if _STACK:
            _STACK[-1] += total
        key = f"{fixturedef.scope}:{fixturedef.argname}"
        _TOTALS[key] += own
        _COUNTS[key] += 1

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(item: Any, nextitem: Any) -> Iterator[None]:
        start = time.perf_counter()
        yield
        _TOTALS["<teardown>"] += time.perf_counter() - start
        _COUNTS["<teardown>"] += 1

    def pytest_sessionfinish(session: Any) -> None:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        folder = Path(_OUT)
        folder.mkdir(parents=True, exist_ok=True)
        rows = {k: [round(v, 2), _COUNTS[k]] for k, v in _TOTALS.most_common()}
        (folder / f"{worker}.json").write_text(json.dumps(rows, indent=0))
