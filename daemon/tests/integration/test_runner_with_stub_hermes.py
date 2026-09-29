"""The skill runner against a stub `hermes` (P1-04, FR-5.11): recorded stream-json becomes
a Result, and a run past its timeout is killed with its whole process group."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from tests.conftest import RECORDINGS, alive, make_run
from tumnis_daemon.runner import execute

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis_daemon.config import DaemonConfig

pytestmark = [pytest.mark.integration]


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
async def test_stream_json_result_becomes_result_message(
    cfg: DaemonConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P1-04-16
    A stub `hermes` replaying recorded stream-json yields a Result with the skill's JSON
    (unfenced), the tokens and the Hermes session id; a failed turn yields `failed`; a
    stub that sleeps past the timeout yields `timed_out` and its process group is gone.
    """
    monkeypatch.setenv("HERMES_STUB_RECORDING", str(RECORDINGS / "enrich_ok.jsonl"))
    run = make_run(prompt="Use the skill enrich.\n<packet>\n{}\n</packet>\n")
    result = await execute(run, cfg)
    assert result.status == "succeeded", result.error
    assert result.run_id == run.run_id
    assert result.correlation_id == run.correlation_id
    assert result.exit_code == 0
    assert result.output_json == {
        "first_action": "Open last month's invoice in Wave and duplicate it",
        "estimate_minutes": 20,
        "acceptance_criteria": ["The March invoice is sent to Acme"],
    }
    assert result.tokens == {"input": 1520, "output": 210}
    assert result.hermes_session_id == "sess-enrich-0001"
    assert result.duration_ms == 8123
    json.loads(result.model_dump_json())  # it serializes

    monkeypatch.setenv("HERMES_STUB_RECORDING", str(RECORDINGS / "plan_ok.jsonl"))
    planned = await execute(make_run(profile="tumnis-master", skill="plan"), cfg)
    assert planned.status == "succeeded"
    assert planned.output_json is not None
    assert "today" in planned.output_json

    monkeypatch.setenv("HERMES_STUB_RECORDING", str(RECORDINGS / "failed_turn.jsonl"))
    failed = await execute(make_run(), cfg)
    assert failed.status == "failed"
    assert failed.output_json is None
    assert failed.error
    assert "rate limited" in failed.error

    pidfile = tmp_path / "pids"
    monkeypatch.setenv("HERMES_STUB_PIDFILE", str(pidfile))
    monkeypatch.setenv("HERMES_STUB_SLEEP", "20")
    started = asyncio.get_running_loop().time()
    late = await execute(make_run(), cfg, timeout_s=0.5)
    assert asyncio.get_running_loop().time() - started < 10
    assert late.status == "timed_out"
    assert late.output_json is None
    pids = [int(pid) for pid in pidfile.read_text().split()]
    assert len(pids) == 2
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while any(alive(pid) for pid in pids):
        assert loop.time() < deadline, f"still running: {[p for p in pids if alive(p)]}"
        await asyncio.sleep(0.05)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
async def test_hermes_stderr_goes_to_devnull(
    cfg: DaemonConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hermes's stderr is never read (nothing uses it), so it goes to /dev/null: a run that
    logs heavily for its whole timeout cannot grow the daemon's memory."""
    where = tmp_path / "stderr"
    monkeypatch.setenv("HERMES_STUB_STDERR_FILE", str(where))
    monkeypatch.setenv("HERMES_STUB_RECORDING", str(RECORDINGS / "enrich_ok.jsonl"))
    result = await execute(make_run(), cfg)
    assert result.status == "succeeded", result.error
    assert where.read_text() == "/dev/null"
