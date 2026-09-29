"""Live updates (P0-22, ADR-0004, R-05): committed changes reach the browsers of their
workspace as `{entity, id}` over `/ws`, never data, so every byte still comes through the
API and row-level security on the refetch.

Writers mark changed rows with `mark_changed(session, entity, id)`, or declare
`__live_entity__ = "<entity>"` on an ORM model (an `after_flush` hook marks new, changed and
deleted instances). Just before the transaction commits, a `before_commit` hook sends one
`pg_notify('tumnis_live', '{"ws": ..., "entity": ..., "id": ...}')` per marked row (the
workspace comes from the transaction's own context); NOTIFY is delivered at commit, so a
rollback tells nobody. Payloads are about 120 bytes, far under Postgres's 8,000.

Each api process runs one `LiveHub`: LISTEN on the direct URL (never PgBouncer), fanning
each notification out to the sockets of its workspace. After a lost connection it closes
every socket (1012), so browsers reconnect and refetch what they missed.
"""

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit
from uuid import UUID

import anyio
import psycopg
from fastapi import WebSocket
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, UOWTransaction
from starlette.websockets import WebSocketDisconnect

from tumnis.core.cache import libpq_url
from tumnis.core.principal import Principal

LIVE_CHANNEL: Final = "tumnis_live"
LIVE_ATTR: Final = "__live_entity__"
PING_S: Final = 25.0  # keeps idle sockets open through proxies (plan default)
POLL_S: Final = 1.0
RETRY_S: Final = 1.0
RETRY_MAX_S: Final = 30.0
QUEUE_MAX: Final = 1000
POLICY_VIOLATION: Final = 1008
SERVICE_RESTART: Final = 1012
# Who may listen: people signed in, and API keys (both read the workspace anyway).
LIVE_KINDS: Final = frozenset({"session", "api_key"})

_MARKS: Final = "tumnis_live_marks"
_NOTIFY = text(
    "SELECT pg_notify(:channel, json_build_object("
    "'ws', current_setting('app.workspace_id', true), "
    "'entity', CAST(:entity AS text), 'id', CAST(:id AS text))::text)"
)
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveMessage:
    entity: str
    id: str


_Queue = asyncio.Queue[LiveMessage]
RESYNC: Final = LiveMessage("", "")  # the hub lost notifications: close, the client refetches


# --- Writers --------------------------------------------------------------------------------


def mark_changed(session: AsyncSession | Session, entity: str, id: UUID) -> None:
    """Announce `entity`/`id` to the workspace's browsers when this transaction commits."""
    sync = session.sync_session if isinstance(session, AsyncSession) else session
    sync.info.setdefault(_MARKS, set()).add((entity, str(id)))


@event.listens_for(Session, "after_flush")
def _mark_models(session: Session, _flush: UOWTransaction) -> None:
    for obj in (*session.new, *session.dirty, *session.deleted):
        entity = getattr(type(obj), LIVE_ATTR, None)
        row_id = getattr(obj, "id", None)
        if isinstance(entity, str) and row_id is not None:
            mark_changed(session, entity, row_id)


@event.listens_for(Session, "before_commit")
def _emit_marked(session: Session) -> None:
    marks: set[tuple[str, str]] | None = session.info.pop(_MARKS, None)
    if not marks:
        return
    connection = session.connection()
    for entity, row_id in sorted(marks):
        connection.execute(_NOTIFY, {"channel": LIVE_CHANNEL, "entity": entity, "id": row_id})


@event.listens_for(Session, "after_rollback")
def _drop_marks(session: Session) -> None:
    session.info.pop(_MARKS, None)


# --- The hub -------------------------------------------------------------------------------


class LiveHub:
    """LISTEN tumnis_live and fan out to the subscribed sockets of each workspace."""

    def __init__(self, url: str) -> None:
        self._dsn = libpq_url(url)
        # Each queue with the event loop of the socket reading it: delivery goes through
        # call_soon_threadsafe, so a socket served on another loop (tests) is safe too.
        self._subscribers: defaultdict[UUID, dict[_Queue, asyncio.AbstractEventLoop]] = defaultdict(
            dict
        )
        self.listening = False

    def subscribe(self, workspace_id: UUID) -> _Queue:
        queue: _Queue = asyncio.Queue(QUEUE_MAX)
        self._subscribers[workspace_id][queue] = asyncio.get_running_loop()
        return queue

    def unsubscribe(self, workspace_id: UUID, queue: _Queue) -> None:
        queues = self._subscribers.get(workspace_id)
        if queues is not None:
            queues.pop(queue, None)
            if not queues:
                del self._subscribers[workspace_id]

    @staticmethod
    def _put(queue: _Queue, message: LiveMessage) -> None:
        with contextlib.suppress(asyncio.QueueFull):  # a stuck socket misses, then resyncs
            queue.put_nowait(message)

    def _deliver(self, queue: _Queue, loop: asyncio.AbstractEventLoop, msg: LiveMessage) -> None:
        with contextlib.suppress(RuntimeError):  # that socket's loop has closed
            loop.call_soon_threadsafe(self._put, queue, msg)

    def dispatch(self, payload: str) -> None:
        try:
            body: Any = json.loads(payload)
            workspace_id = UUID(body["ws"])
            message = LiveMessage(str(body["entity"]), str(body["id"]))
        except (ValueError, KeyError, TypeError):
            return  # no workspace in context, or not ours
        for queue, loop in tuple(self._subscribers.get(workspace_id, {}).items()):
            self._deliver(queue, loop, message)

    def _resync_all(self) -> None:
        for queues in tuple(self._subscribers.values()):
            for queue, loop in tuple(queues.items()):
                self._deliver(queue, loop, RESYNC)

    async def run(self, stop: asyncio.Event) -> None:
        delay = RETRY_S
        while not stop.is_set():
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._dsn, autocommit=True
                ) as conn:
                    # nosemgrep: tumnis-sql-fstring  # LIVE_CHANNEL is a module constant
                    await conn.execute(f"LISTEN {LIVE_CHANNEL}")
                    self.listening = True
                    delay = RETRY_S
                    while not stop.is_set():
                        async for notify in conn.notifies(timeout=POLL_S):
                            self.dispatch(notify.payload)
            except (psycopg.OperationalError, OSError):
                _log.warning("live hub lost its connection; retrying")
            if self.listening:
                self.listening = False
                self._resync_all()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), delay)
            delay = min(delay * 2, RETRY_MAX_S)


# --- The socket ----------------------------------------------------------------------------


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def origin_allowed(origin: str | None, host: str | None, public_base_url: str | None) -> bool:
    """Browsers send cookies on cross-site WebSocket handshakes, so the Origin must be this
    app: PUBLIC_BASE_URL's origin, else the same host the handshake was sent to."""
    if not origin:
        return False
    given = origin.lower().rstrip("/")
    if public_base_url:
        return given == _origin(public_base_url)
    return bool(host) and urlsplit(given).netloc == (host or "").lower()


async def live_socket(websocket: WebSocket) -> None:
    """`WS /ws` (mounted by create_app). 1. Origin must be this app, 2. a session (or
    key) must be signed in, else 1008; 3. accept, then forward `{entity, id}` for this
    workspace only, with a ping every 25 s."""
    settings = getattr(websocket.app.state, "settings", None)
    public_base_url = getattr(settings, "public_base_url", None)
    if not origin_allowed(
        websocket.headers.get("origin"), websocket.headers.get("host"), public_base_url
    ):
        await websocket.close(code=POLICY_VIOLATION)
        return
    principal = getattr(websocket.state, "principal", None)
    if (
        not isinstance(principal, Principal)
        or principal.anonymous
        or principal.workspace_id is None
        or principal.kind not in LIVE_KINDS
    ):
        await websocket.close(code=POLICY_VIOLATION)
        return
    hub: LiveHub = websocket.app.state.live_hub
    queue = hub.subscribe(principal.workspace_id)
    try:
        await websocket.accept()
        await _serve(websocket, queue)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(principal.workspace_id, queue)


async def _serve(websocket: WebSocket, queue: _Queue) -> None:
    """Forward the queue to the socket until either side ends. An anyio task group, so
    a server shutdown's cancellation unwinds both halves cleanly."""
    async with anyio.create_task_group() as group:

        async def forward() -> None:
            while True:
                message: LiveMessage | None = None
                with anyio.move_on_after(PING_S):
                    message = await queue.get()
                if message is None:
                    await websocket.send_json({"type": "ping"})
                elif message is RESYNC:
                    await websocket.close(code=SERVICE_RESTART)
                    break
                else:
                    await websocket.send_json({"entity": message.entity, "id": message.id})
            group.cancel_scope.cancel()

        async def drain() -> None:  # the client sends nothing; this notices it leaving
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass
            group.cancel_scope.cancel()

        group.start_soon(forward)
        group.start_soon(drain)
