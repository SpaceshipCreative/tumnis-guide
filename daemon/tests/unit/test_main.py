"""The daemon refuses to run as root (P1-04, FR-5.11, R-26) and runs a resent `run` once."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tests.conftest import make_run
from tumnis_daemon import main as daemon_main
from tumnis_daemon.state import StateStore

if TYPE_CHECKING:
    from tumnis_daemon.config import DaemonConfig


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
async def test_refuses_to_run_as_root(cfg: DaemonConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P1-04-17
    With geteuid patched to 0, `main` exits 78 before it reads the token or connects.
    """
    connected: list[object] = []

    def connect(*args: object, **kwargs: object) -> object:
        connected.append((args, kwargs))
        raise AssertionError("connected as root")

    monkeypatch.setattr(daemon_main.os, "geteuid", lambda: 0)
    monkeypatch.setattr(daemon_main, "connect", connect)
    cfg.token_file.unlink()  # reading the token would raise first

    with pytest.raises(SystemExit) as exited:
        await daemon_main.main(cfg)
    assert exited.value.code == daemon_main.EX_CONFIG == 78
    assert connected == []


class _Socket:
    """The server side of one connection: the frames it sends, and what the daemon sent."""

    def __init__(self, *frames: str) -> None:
        self.frames = list(frames)
        self.sent: list[str] = []

    def __aiter__(self) -> _Socket:
        return self

    async def __anext__(self) -> str:
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)

    async def send(self, message: str) -> None:
        self.sent.append(message)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
async def test_resent_run_is_not_executed_again(
    cfg: DaemonConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `run` the server sends again (its ack was lost) is acked and not executed again,
    while its result waits for an ack: on the same connection, after a reconnect, and after
    a daemon restart. Skills can have side effects."""
    started: list[object] = []

    async def run_skill(msg: object, state: StateStore, cfg: DaemonConfig) -> None:
        started.append(msg)  # finished: out of `running`, its result kept unacked

    monkeypatch.setattr(daemon_main, "run_skill", run_skill)
    frame = make_run().model_dump_json()
    runs = asyncio.Semaphore(2)
    tasks: set[asyncio.Task[None]] = set()

    async def deliver(state: StateStore, *frames: str) -> _Socket:
        ws = _Socket(*frames)
        await daemon_main.receive_loop(ws, state, runs, tasks, cfg)  # type: ignore[arg-type]
        await asyncio.gather(*tasks)
        return ws

    state = StateStore(cfg.state_dir)
    first = await deliver(state, frame, frame)
    assert len(first.sent) == 2  # both acked
    await deliver(state, frame)  # reconnect
    await deliver(StateStore(cfg.state_dir), frame)  # restart
    assert len(started) == 1
