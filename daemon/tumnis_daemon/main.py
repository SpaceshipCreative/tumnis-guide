"""The daemon's entry point (P1-04, FR-5.11, R-25, R-26).

`tumnis-daemon run --config /etc/tumnis/daemon.toml` refuses to run as root, dials
`<server_url>/ws/runner` with the device token, registers, heartbeats, runs skills and
health checks, and acks every server message (protocol 1: one ack per message). The
websockets reconnect iterator backs off and dials again when the connection drops.
"""

import argparse
import asyncio
import logging
import os
import socket
import sys
from collections.abc import Coroutine
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Final, Literal

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from tumnis_daemon.config import DaemonConfig, load_config
from tumnis_daemon.protocol import (
    Ack,
    HealthCheck,
    InvalidFrame,
    ProtocolError,
    Provision,
    ProvisionResult,
    Registered,
    Run,
    ServerMessage,
    envelope,
    make_ack,
    make_heartbeat,
    make_register,
    parse_server,
)
from tumnis_daemon.runner import check_health, hermes_version, run_skill
from tumnis_daemon.state import StateStore

__all__ = ["EX_CONFIG", "cli", "connect", "main", "os", "refuse_root"]

log = logging.getLogger("tumnis_daemon")

EX_CONFIG: Final = 78  # sysexits: configuration error (running as root)
REGISTER_TIMEOUT_S: Final = 10.0  # plan default
MAX_FRAME: Final = 8 * 1024 * 1024
RECONNECT_PAUSE_S: Final = 1.0  # after a clean close, before dialling again


class ProtocolFailure(RuntimeError):  # noqa: N818  # the plan's word
    """The server answered `register` with something other than `registered`."""


def refuse_root() -> None:
    """R-26: the daemon runs as `tumnis-agent`, never as root (checked before anything)."""
    if os.geteuid() == 0:
        log.error("refusing_to_run_as_root")
        raise SystemExit(EX_CONFIG)


def _daemon_version() -> str:
    try:
        return version("tumnis-daemon")
    except PackageNotFoundError:
        return "0.0.0"


def _os() -> Literal["linux", "darwin"]:
    return "darwin" if sys.platform == "darwin" else "linux"


async def main(cfg: DaemonConfig) -> None:
    refuse_root()
    state = StateStore(cfg.state_dir)
    runs = asyncio.Semaphore(cfg.max_concurrent_runs)
    tasks: set[asyncio.Task[None]] = set()
    hermes = await hermes_version(cfg)
    async for ws in connect(
        f"{cfg.server_url}/ws/runner",
        additional_headers={"Authorization": f"Bearer {cfg.read_token()}"},
        ping_interval=20,
        max_size=MAX_FRAME,
    ):
        try:
            await _session(ws, state=state, runs=runs, tasks=tasks, cfg=cfg, hermes=hermes)
        except* ConnectionClosed:
            log.info("disconnected")
        finally:
            state.ws = None
        await asyncio.sleep(RECONNECT_PAUSE_S)


async def _session(
    ws: ClientConnection,
    *,
    state: StateStore,
    runs: asyncio.Semaphore,
    tasks: set[asyncio.Task[None]],
    cfg: DaemonConfig,
    hermes: str | None,
) -> None:
    register = make_register(
        runner_name=cfg.runner_name,
        host=socket.gethostname(),
        os=_os(),
        daemon_version=_daemon_version(),
        hermes_version=hermes,
        profiles=list(cfg.profiles),
        running_run_ids=state.running_run_ids(),
    )
    await ws.send(register.model_dump_json())
    reg = parse_server(await asyncio.wait_for(ws.recv(), timeout=REGISTER_TIMEOUT_S))
    if not isinstance(reg, Registered):
        raise ProtocolFailure(getattr(reg, "code", reg.type))  # unsupported_protocol_version
    await ws.send(make_ack(reg).model_dump_json())
    state.protocol_version = reg.protocol_version  # 1 until P2-07 ships protocol 2
    log.info("registered", extra={"runner_id": str(reg.runner_id)})
    state.ws = ws
    await state.replay_unacked(ws)  # results the server has not acked
    async with asyncio.TaskGroup() as tg:
        tg.create_task(heartbeat_loop(ws, reg.heartbeat_interval_s, state, cfg))
        tg.create_task(receive_loop(ws, state, runs, tasks, cfg))


async def heartbeat_loop(
    ws: ClientConnection, interval_s: float, state: StateStore, cfg: DaemonConfig
) -> None:
    seq = 0
    while True:
        await ws.send(
            make_heartbeat(cfg.runner_name, seq, state.running_run_ids()).model_dump_json()
        )
        seq += 1
        await asyncio.sleep(interval_s)


async def _guarded(runs: asyncio.Semaphore, work: Coroutine[Any, Any, None]) -> None:
    async with runs:
        await work


def _spawn(tasks: set[asyncio.Task[None]], work: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(work)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def receive_loop(
    ws: ClientConnection,
    state: StateStore,
    runs: asyncio.Semaphore,
    tasks: set[asyncio.Task[None]],
    cfg: DaemonConfig,
) -> None:
    async for frame in ws:
        try:
            msg: ServerMessage = parse_server(frame)
        except InvalidFrame as exc:
            log.warning("invalid_frame", extra={"reason": str(exc)})
            continue
        match msg:
            case Run():
                if msg.run_id not in state.running:  # a resent run already executing
                    _spawn(tasks, _guarded(runs, run_skill(msg, state, cfg)))
            case HealthCheck():
                _spawn(tasks, check_health(msg, state, cfg))
            case Provision():  # P1-06; this daemon does not advertise "provision"
                _spawn(tasks, state.send_reliably(_no_provision(msg)))
            case Ack():
                state.ack(msg.ack_of)
            case ProtocolError():
                log.warning("server_error", extra={"code": msg.code})
            case Registered():
                pass
        if not isinstance(msg, Ack):
            await ws.send(make_ack(msg).model_dump_json())


def _no_provision(msg: Provision) -> ProvisionResult:
    return ProvisionResult(
        **envelope(msg.correlation_id),
        request_id=msg.request_id,
        profile=msg.profile,
        status="failed",
        distribution_version=None,
        error_code="hermes_error",
        error="provisioning is not supported by this daemon version",
    )


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tumnis-daemon")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="dial in to Tumnis and run agent work")
    run.add_argument("--config", type=Path, default=Path("/etc/tumnis/daemon.toml"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    refuse_root()
    asyncio.run(main(load_config(args.config)))


if __name__ == "__main__":
    cli()
