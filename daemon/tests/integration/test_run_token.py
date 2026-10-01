"""The task token reaches the Hermes process (P2-12): `execute` spawns Hermes with the
run's task token as TUMNIS_TOKEN, and with none when the packet carries no token."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import RECORDINGS, make_run
from tumnis_daemon.runner import execute

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis_daemon.config import DaemonConfig

pytestmark = [pytest.mark.integration]


@pytest.mark.req("FR-5.3")
@pytest.mark.wp("P2-12")
async def test_hermes_gets_the_runs_task_token(
    cfg: DaemonConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose packet carries `callback.task_token` spawns Hermes with that value as
    TUMNIS_TOKEN; the daemon's own TUMNIS_TOKEN is never passed on."""
    seen = tmp_path / "token"
    monkeypatch.setenv("HERMES_STUB_RECORDING", str(RECORDINGS / "enrich_ok.jsonl"))
    monkeypatch.setenv("HERMES_STUB_TOKEN_FILE", str(seen))
    monkeypatch.setenv("TUMNIS_TOKEN", "daemon-env-value")

    run = make_run()
    run.packet["callback"] = {"mcp_url": "/mcp", "task_token": "run-task-token-value"}
    result = await execute(run, cfg)
    assert result.status == "succeeded", result.error
    assert seen.read_text() == "run-task-token-value"

    result = await execute(make_run(), cfg)
    assert result.status == "succeeded", result.error
    assert seen.read_text() == "<unset>"
