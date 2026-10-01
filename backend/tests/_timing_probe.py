"""TEMPORARY diagnostic (FIX-ci-integration): per-test, per-fixture and per-worker timings.

Loaded only from the CI integration command with `-p tests._timing_probe`; writes JSON lines
to $TIMING_PROBE_DIR/<worker>.jsonl, and the stacks of every thread for any teardown that
runs past 8 s to $TIMING_PROBE_DIR/<worker>-stacks.txt. Removed before the PR is ready.
"""

from __future__ import annotations

import collections
import faulthandler
import json
import os
import sys
import threading
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


_samples: collections.Counter[str] = collections.Counter()
_cpu: list[tuple[float, float]] = []
_stop = threading.Event()
_KEEP = ("/backend/tumnis/", "/backend/tests/")


def _area(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def _sampler(main_ident: int) -> None:
    """Every 10 ms: the main thread's stack (backend frames plus the innermost frame),
    counted per test module; the collapsed stacks go to <worker>-profile.txt."""
    while not _stop.wait(0.01):
        frame = sys._current_frames().get(main_ident)
        names: list[str] = []
        leaf = True
        while frame is not None:
            code = frame.f_code
            if leaf or any(k in code.co_filename for k in _KEEP):
                short = code.co_filename.rsplit("/backend/", 1)[-1].rsplit("site-packages/", 1)[-1]
                names.append(f"{short}:{code.co_name}")
            leaf = False
            frame = frame.f_back
        names.reverse()
        _samples[_area(_current["nodeid"]) + ";" + ";".join(names[-14:])] += 1


def _cpu_sampler() -> None:
    """Every 2 s: the runner's CPU busy fraction from /proc/stat (gw0 only)."""
    last = None
    while not _stop.wait(2):
        try:
            fields = [float(x) for x in Path("/proc/stat").read_text().split("\n")[0].split()[1:]]
        except OSError:
            return
        idle, total = fields[3] + fields[4], sum(fields)
        if last is not None:
            d_total = total - last[1]
            _cpu.append((time.time(), 1 - (idle - last[0]) / d_total if d_total else 0.0))
        last = (idle, total)


def pytest_sessionstart(session: pytest.Session) -> None:
    if not _DIR or _WORKER == "main":
        return
    if _WORKER == "gw0":
        threading.Thread(target=_cpu_sampler, daemon=True).start()


def pytest_unconfigure(config: pytest.Config) -> None:
    _stop.set()
    _write({"kind": "end", "t": time.time()})
    if _DIR and _WORKER != "main":
        with Path(_DIR, f"{_WORKER}-profile.txt").open("w", encoding="utf-8") as out:
            for stack, count in _samples.most_common():
                out.write(f"{stack} {count}\n")
        _write({"kind": "cpu", "samples": _cpu})
    for handle in (_out, _stacks):
        if handle is not None:
            handle.close()
