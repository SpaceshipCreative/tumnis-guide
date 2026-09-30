"""The daemon's durable state (P1-04, P2-07): the SQLite outbox and seen-set, the runs in
progress and their cancel switches.

Every outbound message goes into the outbox (`<state_dir>/state.db`) before it is sent and
stays until the server acks it. Frames are rendered for the session's protocol at send
time: protocol 2 gets everything as kept; protocol 1 (an older server) gets version-1
results and never `stream`, `status` or `upload_artifact` (those are not even kept while the
session is on protocol 1). On reconnect `replay_unacked` sends the rest again, oldest first,
with the same `message_id`s (the server dedupes on them).
"""

import asyncio
import contextlib
import json
import logging
import shutil
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel
from websockets.exceptions import ConnectionClosed

from tumnis_daemon.outbox import MAX_BYTES, Outbox
from tumnis_daemon.protocol import PROTOCOL_2, PROTOCOL_2_ONLY

log = logging.getLogger(__name__)


class Sender(Protocol):
    async def send(self, message: str, /) -> None: ...


def render(message: dict[str, Any], protocol_version: int) -> str | None:
    """The frame for a kept message on a session of `protocol_version`; None when that
    session cannot carry it."""
    if protocol_version >= PROTOCOL_2:
        return json.dumps(message)
    if message.get("type") in PROTOCOL_2_ONLY:
        return None
    if message.get("type") == "result" and message.get("schema_version") == PROTOCOL_2:
        v1 = {**message, "schema_version": 1}
        if v1.get("status") == "cancelled":  # a protocol-1 result has no cancelled status
            v1["status"] = "failed"
            v1["error"] = v1.get("error") or "cancelled"
        return json.dumps(v1)
    return json.dumps(message)


class StateStore:
    def __init__(self, state_dir: Path, *, outbox_max_bytes: int = MAX_BYTES) -> None:
        self.state_dir = state_dir
        self.runs_dir = state_dir / "runs"
        for d in (state_dir, self.runs_dir):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.outbox = Outbox(state_dir / "state.db", max_bytes=outbox_max_bytes)
        self.running: set[UUID] = set()  # run ids executing now (heartbeats report them)
        # P2-18: the profile of each claimed run, running or waiting for its turn, until
        # `release` (an archive of that profile is refused meanwhile)
        self.run_profiles: dict[UUID, str] = {}
        self.protocol_version = 1  # set from `registered` on every connect
        self.ws: Sender | None = None  # the live connection, None while disconnected
        self._cancels: dict[UUID, tuple[asyncio.Event, list[str]]] = {}
        self._import_legacy(state_dir / "unacked")

    def _import_legacy(self, unacked_dir: Path) -> None:
        """Upgrading from the P1-04 daemon: the results and health reports it kept unacked
        as `<state_dir>/unacked/<message_id>.json` move into the outbox, oldest first. Each
        file goes only once its message is kept (a repeat is ignored by message id); a file
        that does not parse is left for a human."""
        if not unacked_dir.is_dir():
            return
        found: list[tuple[int, Path]] = []
        for path in unacked_dir.glob("*.json"):
            try:
                found.append((path.stat().st_mtime_ns, path))
            except OSError:  # gone since the listing, or a dangling link
                log.warning("legacy_unacked_unreadable", extra={"file": path.name})
        for _, path in sorted(found):
            try:
                message = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(message, dict) or not message.get("message_id"):
                    raise ValueError("not a kept message")
                self.outbox.put(message)
            except (OSError, ValueError, sqlite3.Error):
                log.warning("legacy_unacked_unreadable", extra={"file": path.name})
                continue
            path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            unacked_dir.rmdir()  # only when nothing was left behind

    def close(self) -> None:
        self.outbox.close()

    # --- outbound --------------------------------------------------------------------

    async def send_reliably(self, message: BaseModel) -> None:
        """Keep, then send on the live connection; a dropped send is replayed later. A
        protocol-2 message on a protocol-1 session is neither kept nor sent."""
        data: dict[str, Any] = json.loads(message.model_dump_json())
        if self.protocol_version < PROTOCOL_2 and data.get("type") in PROTOCOL_2_ONLY:
            return
        self.outbox.put(data)
        frame = render(data, self.protocol_version)
        if frame is not None:
            await self._send(frame)

    async def _send(self, frame: str) -> None:
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send(frame)
        except ConnectionClosed:
            log.info("send_deferred")  # kept; replayed on the next connection

    def ack(self, message_id: UUID | str) -> None:
        """Protocol 1: one message acked."""
        self.acked([message_id])

    def acked(self, ids: Iterable[UUID | str]) -> None:
        """The server has these messages (an ack, a batched ack or a nack): forget them.
        A repeated or unknown id is a no-op. The run directory of an acked result goes."""
        for message in self.outbox.ack(str(i) for i in ids):
            if message.get("type") == "result" and message.get("run_id"):
                with contextlib.suppress(ValueError):
                    run_dir = self.runs_dir / str(UUID(str(message["run_id"])))
                    shutil.rmtree(run_dir, ignore_errors=True)

    def unacked(self) -> list[str]:
        """The kept frames as this session's protocol would send them, oldest first."""
        frames = (render(m, self.protocol_version) for m in self.outbox.unacked())
        return [frame for frame in frames if frame is not None]

    async def replay_unacked(self, ws: Sender) -> None:
        """Send every kept frame again, oldest first. On a protocol-1 session, messages it
        cannot carry are dropped from the outbox (they would never be acked)."""
        for message in self.outbox.unacked():
            frame = render(message, self.protocol_version)
            if frame is None:
                self.outbox.ack([str(message["message_id"])])
                continue
            await ws.send(frame)

    # --- runs ------------------------------------------------------------------------

    def claim_run(self, run_id: UUID, message_id: UUID | None = None) -> bool:
        """True the first time this daemon sees `run_id` (or the `run` message itself): the
        seen-set in the outbox's database is the durable record, so a `run` the server
        resends (its ack was lost), before or after a daemon restart, is never executed
        twice; its result waits in the outbox until acked. A run cut off by a restart is not
        started again; the server times it out."""
        if run_id in self.running or self.outbox.seen_run(str(run_id)):
            return False
        if message_id is not None and self.outbox.seen_command(str(message_id)):
            return False
        self.outbox.mark_command(str(message_id or f"run:{run_id}"), str(run_id))
        self.running.add(run_id)
        return True

    def running_run_ids(self) -> list[UUID]:
        return sorted(self.running)

    def cancel_switch(self, run_id: UUID) -> asyncio.Event:
        """The run's cancel switch (the runner waits on it) until `release`."""
        switch = self._cancels.get(run_id)
        if switch is None:
            switch = self._cancels[run_id] = (asyncio.Event(), [])
        return switch[0]

    def cancel_reason(self, run_id: UUID) -> str | None:
        switch = self._cancels.get(run_id)
        return switch[1][0] if switch is not None and switch[1] else None

    def release(self, run_id: UUID) -> None:
        self._cancels.pop(run_id, None)
        self.run_profiles.pop(run_id, None)

    def busy_profiles(self) -> frozenset[str]:
        """The profiles with a claimed run still in progress (P2-18)."""
        return frozenset(p for r, p in self.run_profiles.items() if r in self.running)

    def cancel(self, run_id: UUID, reason: str) -> bool:
        """Flip the run's cancel switch; False when the run is not running here."""
        switch = self._cancels.get(run_id)
        if switch is None:
            return False
        event, reasons = switch
        if not reasons:
            reasons.append(reason)
        event.set()
        return True
