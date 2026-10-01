"""A run's environment for Hermes (P2-12): the clean environment plus the run's own task
token as TUMNIS_TOKEN, which the profile's `tumnis` MCP server sends as its bearer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import make_run
from tumnis_daemon.runner import clean_env, run_env

if TYPE_CHECKING:
    from tumnis_daemon.config import DaemonConfig

DAEMON_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/home/tumnis-agent",
    "LANG": "C.UTF-8",
    "HERMES_HOME": "/home/tumnis-agent/.hermes",
    "TUMNIS_TOKEN": "daemon-env-value",
    "AWS_SECRET_ACCESS_KEY": "nope",
}


@pytest.mark.req("FR-5.3")
@pytest.mark.wp("P2-12")
def test_run_env_carries_the_runs_task_token(cfg: DaemonConfig) -> None:
    """The packet's `callback.task_token` becomes TUMNIS_TOKEN; everything else is the
    clean environment, and the daemon's own TUMNIS_TOKEN never reaches Hermes."""
    run = make_run()
    run.packet["callback"] = {"mcp_url": "/mcp", "task_token": "run-task-token-value"}
    env = run_env(cfg, run, DAEMON_ENV)
    assert env == {**clean_env(cfg, DAEMON_ENV), "TUMNIS_TOKEN": "run-task-token-value"}
    assert "AWS_SECRET_ACCESS_KEY" not in env


@pytest.mark.req("FR-5.3")
@pytest.mark.wp("P2-12")
@pytest.mark.parametrize(
    "callback",
    [None, {"mcp_url": "/mcp", "task_token": None}, {"task_token": ""}, "not-a-mapping"],
)
def test_run_env_without_a_task_token_sets_none(cfg: DaemonConfig, callback: object) -> None:
    """A packet without a task token (a phase 1 skill run) gets no TUMNIS_TOKEN at all."""
    run = make_run()
    if callback is not None:
        run.packet["callback"] = callback
    env = run_env(cfg, run, DAEMON_ENV)
    assert env == clean_env(cfg, DAEMON_ENV)
    assert "TUMNIS_TOKEN" not in env
