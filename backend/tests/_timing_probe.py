"""TEMPORARY diagnostic (FIX-ci-integration): per-test, per-fixture and per-worker timings.

Loaded only from the CI integration command with `-p tests._timing_probe`; writes JSON lines
to $TIMING_PROBE_DIR/<worker>.jsonl, and the stacks of every thread for any teardown that
runs past 8 s to $TIMING_PROBE_DIR/<worker>-stacks.txt. Removed before the PR is ready.
"""

from __future__ import annotations

import faulthandler
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from _pytest.fixtures import FixtureDef

_DIR = os.environ.get("TIMING_PROBE_DIR")
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
_out: Any = None
_stacks: Any = None
_current: dict[str, str] = {"nodeid": ""}


def _write(record: dict[str, Any]) -> None:
    if _out is not None:
        _out.write(json.dumps(record) + "\n")
        _out.flush()


def pytest_configure(config: pytest.Config) -> None:
    global _out, _stacks  # noqa: PLW0603
    if not _DIR:
        return
    Path(_DIR).mkdir(parents=True, exist_ok=True)
    _out = Path(_DIR, f"{_WORKER}.jsonl").open("a", encoding="utf-8")  # noqa: SIM115
    _stacks = Path(_DIR, f"{_WORKER}-stacks.txt").open("a", encoding="utf-8")  # noqa: SIM115
    _write({"kind": "start", "t": time.time()})
    original_finish = FixtureDef.finish

    def finish(self: FixtureDef[Any], request: Any) -> None:
        started = time.perf_counter()
        try:
            original_finish(self, request)
        finally:
            spent = time.perf_counter() - started
            if spent >= 0.05:
                _write(
                    {
                        "kind": "fix_td",
                        "fixture": self.argname,
                        "scope": self.scope,
                        "s": round(spent, 3),
                        "test": _current["nodeid"],
                    }
                )

    FixtureDef.finish = finish  # type: ignore[method-assign]


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef: FixtureDef[Any], request: Any) -> Any:
    started = time.perf_counter()
    yield
    spent = time.perf_counter() - started
    if spent >= 0.05:
        _write(
            {
                "kind": "fix_su",
                "fixture": fixturedef.argname,
                "scope": fixturedef.scope,
                "s": round(spent, 3),
                "test": _current["nodeid"],
            }
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
    _current["nodeid"] = item.nodeid
    started = time.time()
    yield
    _write({"kind": "test", "nodeid": item.nodeid, "t0": started, "t1": time.time()})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
    if _stacks is not None:
        _stacks.write(f"\n=== teardown {item.nodeid} at {time.time():.1f}\n")
        _stacks.flush()
        faulthandler.dump_traceback_later(8, file=_stacks)
    try:
        yield
    finally:
        if _stacks is not None:
            faulthandler.cancel_dump_traceback_later()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _write(
        {
            "kind": "phase",
            "nodeid": report.nodeid,
            "when": report.when,
            "s": round(report.duration, 3),
            "outcome": report.outcome,
        }
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    _write({"kind": "end", "t": time.time()})
