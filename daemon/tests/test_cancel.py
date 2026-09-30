"""Cancel (P2-07, FR-5.11): the run's whole process group gets SIGTERM, then SIGKILL once the
grace period (10 s, plan default) runs out, and the run ends with a `cancelled` result."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from tests.conftest import alive, make_run

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis_daemon.config import DaemonConfig

GRACE_S = 0.5


def _read_pids(pidfile: Path) -> list[int]:
    words = pidfile.read_text().split() if pidfile.exists() else []
    return [int(pid) for pid in words] if len(words) >= 2 else []


async def _pids(pidfile: Path) -> list[int]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while not (pids := _read_pids(pidfile)):
        assert loop.time() < deadline, "the stub never started"
        await asyncio.sleep(0.02)
    return pids


async def _gone(pids: list[int]) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while any(alive(pid) for pid in pids):
        assert loop.time() < deadline, f"still running: {[p for p in pids if alive(p)]}"
        await asyncio.sleep(0.02)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
@pytest.mark.xfail(strict=True, reason="spec:P2-07")
async def test_cancel_terminates_process_group(
    cfg: DaemonConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P2-07-06
    A cancel sends SIGTERM to the run's process group first; a Hermes that ignores it is
    killed with SIGKILL when the grace period runs out (10 s by default), and the whole
    group is gone. The run ends with a protocol-2 `result` whose status is `cancelled`.
    Through `run_skill`, a cancel for a running run is answered with a `status`
    `cancelling` and then that `cancelled` result, both kept until acked.
    """
    from tumnis_daemon import runner  # noqa: PLC0415
    from tumnis_daemon.state import StateStore  # noqa: PLC0415

    assert runner.KILL_GRACE_S == 10.0
    loop = asyncio.get_running_loop()

    # Hermes notes SIGTERM and keeps running: SIGKILL after the grace period.
    pidfile, signals = tmp_path / "pids", tmp_path / "signals"
    monkeypatch.setenv("HERMES_STUB_PIDFILE", str(pidfile))
    monkeypatch.setenv("HERMES_STUB_SLEEP", "30")
    monkeypatch.setenv("HERMES_STUB_SIGNALS", str(signals))
    cancel = asyncio.Event()
    task = asyncio.create_task(runner.execute(make_run(), cfg, cancel=cancel, kill_grace_s=GRACE_S))
    pids = await _pids(pidfile)
    started = loop.time()
    cancel.set()
    result = await asyncio.wait_for(task, 10)
    assert loop.time() - started >= GRACE_S
    assert signals.read_text().split() == ["SIGTERM"]
    assert result.status == "cancelled"
    assert result.schema_version == 2
    assert result.output_json is None
    await _gone(pids)

    # Hermes that stops on SIGTERM ends at once.
    pidfile.unlink()
    monkeypatch.delenv("HERMES_STUB_SIGNALS")
    cancel = asyncio.Event()
    task = asyncio.create_task(runner.execute(make_run(), cfg, cancel=cancel, kill_grace_s=5))
    pids = await _pids(pidfile)
    started = loop.time()
    cancel.set()
    result = await asyncio.wait_for(task, 10)
    assert loop.time() - started < 5
    assert result.status == "cancelled"
    await _gone(pids)

    # Through run_skill: status `cancelling`, then the `cancelled` result.
    pidfile.unlink()
    state = StateStore(cfg.state_dir)
    state.protocol_version = 2
    run = make_run()
    task = asyncio.create_task(runner.run_skill(run, state, cfg))
    pids = await _pids(pidfile)
    assert state.cancel(run.run_id, "stopped by the user") is True
    await asyncio.wait_for(task, 20)
    await _gone(pids)
    kept = [json.loads(frame) for frame in state.unacked()]
    ours = [m for m in kept if m.get("run_id") == str(run.run_id)]
    states = [m["state"] for m in ours if m["type"] == "status"]
    assert "cancelling" in states
    (result_frame,) = [m for m in ours if m["type"] == "result"]
    assert result_frame["status"] == "cancelled"
    assert ours.index(result_frame) > max(
        i for i, m in enumerate(ours) if m["type"] == "status" and m["state"] == "cancelling"
    )
    assert state.cancel(run.run_id, "again") is False  # no longer running
