"""The real runner daemon against the api (P2-07, FR-5.11): the `tumnis-daemon` package runs
as a subprocess, dials the app served by uvicorn on a free port, and streams a run from a
stub `hermes` to the run view.

The daemon is its own uv project and never imports the backend; the backend's environment
already holds its only dependencies (pydantic and websockets, pinned alike), so the test
runs it with `PYTHONPATH=daemon`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER
from tests.fakes.fake_runner import create_runner, register_profile

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[3]
DAEMON = REPO / "daemon"
STUB_HERMES = DAEMON / "tests" / "stubs" / "hermes"
RECORDING = DAEMON / "tests" / "recordings" / "stream_json" / "three_lines.jsonl"
LINES = [
    "Reading the packet for the enrich skill.",
    "Looking up last month's invoice.",
    "Drafting the first action.",
]
WAIT_S = 30.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


async def _until(what: str, check: Any) -> Any:
    deadline = time.monotonic() + WAIT_S
    while True:
        found = await check() if asyncio.iscoroutinefunction(check) else check()
        if found:
            return found
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.1)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
async def test_real_daemon_streams_to_run_view(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls, tmp_path: Path
) -> None:
    """T-P2-07-15
    The real daemon package, started as `tumnis-daemon run`, registers on protocol 2, runs
    a dispatched skill with the stub `hermes` (three assistant lines, then a JSON result),
    and the run view returns the three lines in order, then the result.
    """
    import uvicorn  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api  # noqa: PLC0415
    from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
        DaemonTransport,
        HermesAgent,
    )
    from tumnis.modules.agents.tests.contract.base import make_packet  # noqa: PLC0415

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    serving = asyncio.create_task(server.serve())
    await _until("uvicorn", lambda: server.started)

    runner_id, token = create_runner(workspace, clock, "homelab-hermes")
    profile_id = register_profile(workspace, clock, "acme-site", runner_id=runner_id)
    token_file = tmp_path / "runner.token"
    token_file.write_text(token + "\n")
    token_file.chmod(0o600)
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "daemon.toml"
    config.write_text(
        f'server_url = "ws://127.0.0.1:{port}"\n'
        'runner_name = "homelab-hermes"\n'
        f'token_file = "{token_file}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'hermes_bin = "{STUB_HERMES}"\n'
        'profiles = ["acme-site"]\n'
        f'agent_home = "{home}"\n'
    )
    STUB_HERMES.chmod(STUB_HERMES.stat().st_mode | 0o100)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(DAEMON),
        "HERMES_STUB_RECORDING": str(RECORDING),
    }
    daemon = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tumnis_daemon.main", "run", "--config", str(config), env=env
    )
    try:
        await _until(
            "the daemon's protocol-2 register",
            lambda: _owner(
                db,
                "SELECT 1 FROM runners"
                " WHERE id = %s AND status = 'online' AND protocol_version = 2",
                (runner_id,),
            ),
        )
        packet = make_packet(profile_id)
        await HermesAgent(profile_id, DaemonTransport(workspace.ctx, clock)).dispatch(packet)

        async def result_in_view() -> list[Any]:
            async with tenant_session(workspace.ctx) as s:
                view = await api.run_log(s, packet.run_id)
            return view if any(event.kind == "result" for event in view) else []

        view = await _until("the run's result in the run view", result_in_view)
        logs = [event.payload["text"] for event in view if event.kind == "log"]
        assert logs == LINES
        kinds = [event.kind for event in view]
        assert kinds.index("result") > max(i for i, k in enumerate(kinds) if k == "log")
        result = next(event for event in view if event.kind == "result")
        assert result.payload["status"] == "succeeded"
        assert result.payload["output_json"]["estimate_minutes"] == 20
    finally:
        if daemon.returncode is None:
            daemon.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(daemon.wait(), 10)
            if daemon.returncode is None:
                daemon.kill()
                await daemon.wait()
        server.should_exit = True
        await asyncio.wait_for(serving, 15)
