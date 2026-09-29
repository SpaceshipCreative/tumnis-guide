"""The daemon's durable state (P1-04): outbound frames kept until the server acks them.

Every Result and HealthReport is written to `<state_dir>/unacked/<message_id>.json` before it
is sent, and removed when its `ack` arrives. On reconnect `replay_unacked` sends the rest
again with the same `message_id` (the server dedupes on it), so a result survives a dropped
socket or a daemon restart. The run directory of an acked result is removed with it.
"""

import contextlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)


class Sender(Protocol):
    async def send(self, message: str, /) -> None: ...


class StateStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.unacked_dir = state_dir / "unacked"
        self.runs_dir = state_dir / "runs"
        for d in (state_dir, self.unacked_dir, self.runs_dir):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.running: set[UUID] = set()  # run ids executing now (heartbeats report them)
        self.protocol_version = 1  # set from `registered` on every connect
        self.ws: Sender | None = None  # the live connection, None while disconnected

    def _path(self, message_id: UUID) -> Path:
        return self.unacked_dir / f"{message_id}.json"

    async def send_reliably(self, message: BaseModel) -> None:
        """Persist, then send on the live connection; a dropped send is replayed later."""
        data = message.model_dump_json()
        message_id = UUID(str(message.model_dump()["message_id"]))
        path = self._path(message_id)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        tmp.replace(path)
        await self._send(data)

    async def _send(self, data: str) -> None:
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send(data)
        except ConnectionClosed:
            log.info("send_deferred")  # kept on disk; replayed on the next connection

    def ack(self, message_id: UUID) -> None:
        path = self._path(message_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return  # an ack of a frame that was not kept (register, heartbeat, ack)
        except ValueError:
            data = {}
        path.unlink(missing_ok=True)
        run_id = data.get("run_id") if data.get("type") == "result" else None
        if run_id:
            with contextlib.suppress(ValueError):
                shutil.rmtree(self.runs_dir / str(UUID(str(run_id))), ignore_errors=True)

    def unacked(self) -> list[str]:
        files = sorted(self.unacked_dir.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        return [p.read_text(encoding="utf-8") for p in files]

    async def replay_unacked(self, ws: Sender) -> None:
        """Send every kept frame again, oldest first."""
        for data in self.unacked():
            await ws.send(data)

    def claim_run(self, run_id: UUID) -> bool:
        """True the first time this daemon sees `run_id`; its run directory is the durable
        record. The directory goes when the run's result is acked, so a `run` the server
        resends (its ack was lost) is not executed twice while its result is kept. A run cut
        off by a daemon restart is not started again; the server times it out."""
        run_dir = self.runs_dir / str(run_id)
        if run_id in self.running or run_dir.exists():
            return False
        run_dir.mkdir(mode=0o700, parents=True)
        self.running.add(run_id)
        return True

    def running_run_ids(self) -> list[UUID]:
        return sorted(self.running)
