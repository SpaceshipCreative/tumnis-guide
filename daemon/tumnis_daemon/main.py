"""The daemon's entry point (P1-04, P2-07, FR-5.11, R-25, R-26).

`tumnis-daemon run --config /etc/tumnis/daemon.toml` refuses to run as root (exit 78, before
it reads anything), removes worktrees a crash left behind, then dials `<server_url>/ws/runner`
with the device token, registers (protocols 1 and 2), replays its outbox, heartbeats, runs
skills, cancels them, answers health checks and archives, restores and purges profiles
(P2-18; `register` lists the live profiles only). Server messages are acked in the session's
style: `ack{ack_of}` on protocol 1, `ack{message_ids}` on protocol 2. The websockets
reconnect iterator backs off and dials again when the connection drops
(https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html); a fatal
handshake error ends the loop and the unit's restart policy takes over.
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

from tumnis_daemon import archive
from tumnis_daemon.config import DaemonConfig, load_config
from tumnis_daemon.protocol import (
    PROTOCOL_2,
    Ack,
    AckBatch,
    Archive,
    Cancel,
    HealthCheck,
    InvalidFrame,
    Nack,
    ProtocolError,
    Provision,
    PurgeArchive,
    Registered,
    Restore,
    Run,
    ServerMessage,
    make_ack,
    make_ack_batch,
    make_heartbeat,
    make_register,
    parse_server,
)
from tumnis_daemon.provision import provision
from tumnis_daemon.runner import check_health, hermes_version, run_skill
from tumnis_daemon.state import Sender, StateStore
from tumnis_daemon.worktree import cleanup_stale_worktrees

__all__ = ["EX_CONFIG", "cli", "connect", "main", "os", "refuse_root"]

log = logging.getLogger("tumnis_daemon")

EX_CONFIG: Final = 78  # sysexits: configuration error (running as root)
REGISTER_TIMEOUT_S: Final = 10.0  # plan default
MAX_FRAME: Final = 2 * 1024 * 1024  # plan default: 2 MiB per frame
RECONNECT_PAUSE_S: Final = 1.0  # after a clean close, before dialling again
ROOT_MESSAGE: Final = "tumnis-daemon: refusing to run as root; run it as tumnis-agent"


class ProtocolFailure(RuntimeError):  # noqa: N818  # the plan's word
    """The server answered `register` with something other than `registered`."""


def refuse_root() -> None:
    """R-26: the daemon runs as `tumnis-agent`, never as root (checked before anything)."""
    if os.geteuid() == 0:
        print(ROOT_MESSAGE, file=sys.stderr)  # before logging is set up
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
    removed = await asyncio.to_thread(cleanup_stale_worktrees, cfg)
    if removed:
        log.info("stale_worktrees_removed", extra={"count": len(removed)})
    state = StateStore(cfg.state_dir, outbox_max_bytes=cfg.outbox_max_bytes)
    runs = asyncio.Semaphore(cfg.max_concurrent_runs)
    tasks: set[asyncio.Task[None]] = set()
    hermes = await hermes_version(cfg)
    try:
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
    finally:
        state.close()


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
        profiles=archive.live_profiles(cfg),
        running_run_ids=state.running_run_ids(),
    )
    await ws.send(register.model_dump_json())
    reg = parse_server(await asyncio.wait_for(ws.recv(), timeout=REGISTER_TIMEOUT_S))
    if not isinstance(reg, Registered):
        raise ProtocolFailure(getattr(reg, "code", reg.type))  # unsupported_protocol_version
    state.protocol_version = reg.protocol_version
    await ack(ws, state, reg)
    log.info("registered", extra={"runner_id": str(reg.runner_id)})
    state.ws = ws
    await state.replay_unacked(ws)  # everything the server has not acked, in order
    async with asyncio.TaskGroup() as tg:
        tg.create_task(heartbeat_loop(ws, reg.heartbeat_interval_s, state, cfg))
        tg.create_task(receive_loop(ws, state, runs, tasks, cfg))


async def heartbeat_loop(
    ws: ClientConnection, interval_s: float, state: StateStore, cfg: DaemonConfig
) -> None:
    seq = 0
    while True:
        depth = state.outbox.depth() if state.protocol_version >= PROTOCOL_2 else None
        beat = make_heartbeat(cfg.runner_name, seq, state.running_run_ids(), depth)
        await ws.send(beat.model_dump_json())
        seq += 1
        await asyncio.sleep(interval_s)


async def ack(ws: Sender, state: StateStore, message: ServerMessage) -> None:
    """Ack a server message the session's way."""
    if state.protocol_version >= PROTOCOL_2:
        frame = make_ack_batch(message.correlation_id, [message.message_id])
        await ws.send(frame.model_dump_json())
    else:
        await ws.send(make_ack(message).model_dump_json())


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
        _handle(msg, state, runs, tasks, cfg)
        if not isinstance(msg, Ack | AckBatch | Nack):
            await ack(ws, state, msg)


def _handle(
    msg: ServerMessage,
    state: StateStore,
    runs: asyncio.Semaphore,
    tasks: set[asyncio.Task[None]],
    cfg: DaemonConfig,
) -> None:
    match msg:
        case Run():  # both versions; a resent run: running, or its result kept
            if state.claim_run(msg.run_id, msg.message_id):
                state.run_profiles[msg.run_id] = msg.profile
                _spawn(tasks, _guarded(runs, run_skill(msg, state, cfg)))
        case Cancel():
            if not state.cancel(msg.run_id, msg.reason):
                log.info("cancel_for_idle_run", extra={"run_id": str(msg.run_id)})
        case HealthCheck():
            _spawn(tasks, check_health(msg, state, cfg))
        case Provision():  # P1-06
            _spawn(tasks, provision(msg, state, cfg))
        case Ack():
            state.ack(msg.ack_of)
        case AckBatch():
            state.acked(msg.message_ids)
        case Nack():  # refused for good: drop it from the outbox
            log.warning("nacked", extra={"code": msg.code})
            state.acked([msg.nack_of])
        case Archive() | Restore() | PurgeArchive():  # P2-18
            _spawn(tasks, archive.dispatch(msg, state, cfg))
        case ProtocolError():
            log.warning("server_error", extra={"code": msg.code})
        case Registered():
            pass


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tumnis-daemon")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="dial in to Tumnis and run agent work")
    run.add_argument("--config", type=Path, default=Path("/etc/tumnis/daemon.toml"))
    args = parser.parse_args(argv)
    refuse_root()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main(load_config(args.config)))


if __name__ == "__main__":
    cli()
