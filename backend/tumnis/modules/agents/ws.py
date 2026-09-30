"""`WS /ws/runner` (P1-04, FR-5.11, R-25): where runner daemons dial in, and the mailbox
forwarder between the worker and their sockets.

1. The device token (`Authorization: Bearer tmd_...`, resolved by the authentication
   middleware) is checked before `accept()`: missing or refused, the handshake is closed
   with 1008 and `auth.failed` is audited (actor type `device`) in the workspace, when the
   deployment is self-hosted with one workspace; otherwise it is only logged, since no
   workspace can be named for an unknown token.
2. The first frame must be `register` within `app.state.runner_register_timeout_s` (10 s),
   naming the token's runner and sharing a protocol version with the server; otherwise an
   `error` and a close with 1008.
3. `registered`, the runner row updated (online, inventory), then every queued or
   unacknowledged mailbox row for the runner, oldest first.
4. A reader (parse, dedupe on `message_id`, write, ack every message; three invalid frames
   in a row close the socket) and a forwarder (woken by `NOTIFY runner_mailbox` through the
   `RunnerHub`, with a 1 s poll as a backstop) run until either side closes.

The api never calls out: the daemon dialled in. The worker never touches the socket; it
writes mailbox rows and NOTIFYs. A result is written as a run event and handed to its
waiting `run_skill` workflow with `DBOSClient.send` (idempotent on the message id) before
it is acked, so a lost ack means a resend that lands once. A disconnect does not mark the
runner offline; only the runner sweep does, from the heartbeats.
"""

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid4, uuid5

import psycopg
from fastapi import WebSocket
from psycopg import sql
from sqlalchemy import Table, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from starlette.websockets import WebSocketDisconnect, WebSocketState

from tumnis.core import audit, db
from tumnis.core.cache import libpq_url
from tumnis.core.live import mark_changed
from tumnis.core.principal import Principal
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import ActorRef
from tumnis.modules.agents import api
from tumnis.modules.agents.models import RunEventRow, RunnerMessage, RunRow
from tumnis.modules.agents.models import Runner as RunnerModel
from tumnis.modules.agents.protocol import (
    SERVER_PROTOCOL_VERSIONS,
    Ack,
    DaemonMessage,
    ErrorCode,
    HealthReport,
    Heartbeat,
    InvalidMessage,
    ProtocolError,
    Register,
    Registered,
    Result,
    negotiate,
    parse_daemon,
)

if TYPE_CHECKING:
    from dbos import DBOSClient

REGISTER_TIMEOUT_S: Final = 10.0  # plan default; app.state.runner_register_timeout_s
MAX_INVALID: Final = 3  # invalid frames in a row before the socket closes (plan default)
POLL_S: Final = 1.0
STOP_S: Final = 5.0  # how long a closing socket waits for its forwarder's turn to end
RETRY_S: Final = 1.0
RETRY_MAX_S: Final = 30.0
POLICY_VIOLATION: Final = 1008
NIL: Final = UUID(int=0)

_runners: Table = RunnerModel.__table__  # type: ignore[assignment]
_messages: Table = RunnerMessage.__table__  # type: ignore[assignment]
_runs: Table = RunRow.__table__  # type: ignore[assignment]
_events: Table = RunEventRow.__table__  # type: ignore[assignment]
_NOTIFY = text("SELECT pg_notify(:channel, :payload)")
_log = logging.getLogger(__name__)


# --- The hub: LISTEN runner_mailbox, wake the runner's socket ------------------------------


class _Waiter:
    """One socket's wake-up: an asyncio.Event on the socket's own loop."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.close = False

    def _wake(self, close: bool) -> None:
        self.close = self.close or close
        self.event.set()

    def wake(self, close: bool) -> None:
        with contextlib.suppress(RuntimeError):  # that socket's loop has closed
            self.loop.call_soon_threadsafe(self._wake, close)


class RunnerHub:
    """Each api process runs one: LISTEN runner_mailbox on the direct URL and wake the
    socket of the runner a notification names. It also holds the process's DBOSClient,
    through which results and health reports reach their workflows."""

    def __init__(self, url: str, dbos_system_url: str | None) -> None:
        self._dsn = libpq_url(url)
        self._dbos_url = dbos_system_url
        self._client: DBOSClient | None = None
        self._sockets: defaultdict[UUID, set[_Waiter]] = defaultdict(set)
        self.listening = False

    def client(self) -> "DBOSClient":
        if self._client is None:
            from dbos import DBOSClient  # noqa: PLC0415

            self._client = DBOSClient(system_database_url=self._dbos_url, lazy=True)
        return self._client

    def subscribe(self, runner_id: UUID) -> _Waiter:
        waiter = _Waiter()
        self._sockets[runner_id].add(waiter)
        return waiter

    def unsubscribe(self, runner_id: UUID, waiter: _Waiter) -> None:
        waiters = self._sockets.get(runner_id)
        if waiters is not None:
            waiters.discard(waiter)
            if not waiters:
                del self._sockets[runner_id]

    def dispatch(self, payload: str) -> None:
        try:
            body: Any = json.loads(payload)
            runner_id = UUID(body["runner"])
            close = bool(body.get("close", False))
        except (ValueError, KeyError, TypeError):
            return
        for waiter in tuple(self._sockets.get(runner_id, ())):
            waiter.wake(close)

    async def run(self, stop: asyncio.Event) -> None:
        delay = RETRY_S
        try:
            while not stop.is_set():
                try:
                    async with await psycopg.AsyncConnection.connect(
                        self._dsn, autocommit=True
                    ) as conn:
                        await conn.execute(
                            sql.SQL("LISTEN {}").format(sql.Identifier(api.RUNNER_CHANNEL))
                        )
                        self.listening = True
                        delay = RETRY_S
                        while not stop.is_set():
                            async for notify in conn.notifies(timeout=POLL_S):
                                self.dispatch(notify.payload)
                except (psycopg.OperationalError, OSError):
                    _log.warning("runner hub lost its connection; retrying")
                self.listening = False
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), delay)
                delay = min(delay * 2, RETRY_MAX_S)
        finally:
            if self._client is not None:
                client, self._client = self._client, None
                await asyncio.to_thread(client.destroy)


# --- Refusals before accept ----------------------------------------------------------------


async def _audit_refusal(websocket: WebSocket, reason: str) -> None:
    """`auth.failed` for a refused device handshake, in the one workspace of a self-hosted
    deployment; only logged when no single workspace can be named."""
    app = websocket.app
    settings = getattr(app.state, "settings", None)
    _log.warning("runner handshake refused", extra={"reason": reason})
    if getattr(settings, "deployment_mode", "self-hosted") != "self-hosted":
        return
    try:
        async with db.app_sessionmaker()() as s, s.begin():
            workspaces = await audit.workspace_ids(s)
        if len(workspaces) != 1:
            return
        ctx = WorkspaceContext(workspaces[0], ActorRef(f"device:{NIL}"))
        async with tenant_session(ctx) as s:
            await audit.record(
                s,
                "auth.failed",
                details={"reason": reason, "channel": "ws_runner"},
                occurred_at=app.state.clock.now(),
            )
    except Exception:  # an audit failure must not keep the socket open
        _log.exception("could not audit a refused runner handshake")


def _device(websocket: WebSocket) -> Principal | None:
    principal = getattr(websocket.state, "principal", None)
    if (
        isinstance(principal, Principal)
        and principal.kind == "device"
        and not principal.anonymous
        and principal.subject_id is not None
    ):
        return principal
    return None


def _refusal_reason(websocket: WebSocket) -> str:
    header = websocket.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return "missing_token"
    return "invalid_token"


# --- The socket ----------------------------------------------------------------------------


async def runner_socket(websocket: WebSocket) -> None:
    """`WS /ws/runner` (mounted by create_app)."""
    principal = _device(websocket)
    if principal is None or principal.workspace_id is None or principal.subject_id is None:
        await _audit_refusal(websocket, _refusal_reason(websocket))
        await websocket.close(code=POLICY_VIOLATION)
        return
    runner_id = principal.subject_id
    ctx = WorkspaceContext(principal.workspace_id, ActorRef(f"device:{runner_id}"))
    hub: RunnerHub = websocket.app.state.runner_hub
    waiter = hub.subscribe(runner_id)
    try:
        await websocket.accept()
        await _RunnerSocket(websocket, ctx, runner_id, hub, waiter).serve()
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(runner_id, waiter)


class _RunnerSocket:
    def __init__(
        self,
        websocket: WebSocket,
        ctx: WorkspaceContext,
        runner_id: UUID,
        hub: RunnerHub,
        waiter: _Waiter,
    ) -> None:
        self.ws = websocket
        self.ctx = ctx
        self.runner_id = runner_id
        self.hub = hub
        self.waiter = waiter
        self.clock = websocket.app.state.clock
        self._send_lock = asyncio.Lock()

    # --- sending -------------------------------------------------------------------------

    def _envelope(self, correlation_id: str) -> dict[str, Any]:
        return {"message_id": uuid4(), "correlation_id": correlation_id, "sent_at": self.now()}

    def now(self) -> datetime:
        now: datetime = self.clock.now()
        return now

    async def _send_text(self, frame: str) -> None:
        async with self._send_lock:
            await self.ws.send_text(frame)

    async def _error(self, code: ErrorCode, detail: str) -> None:
        error = ProtocolError(
            **self._envelope(f"runner:{self.runner_id}"), code=code, detail=detail
        )
        await self._send_text(error.model_dump_json())

    async def _refuse(self, code: ErrorCode, detail: str) -> None:
        await self._error(code, detail)
        await self._close()

    async def _close(self) -> None:
        if self.ws.application_state != WebSocketState.DISCONNECTED:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await self.ws.close(code=POLICY_VIOLATION)

    async def _ack(self, message: DaemonMessage) -> None:
        ack = Ack(**self._envelope(message.correlation_id), ack_of=message.message_id)
        await self._send_text(ack.model_dump_json())

    # --- the session ---------------------------------------------------------------------

    async def serve(self) -> None:
        timeout = float(getattr(self.ws.app.state, "runner_register_timeout_s", REGISTER_TIMEOUT_S))
        try:
            first = await asyncio.wait_for(self.ws.receive_text(), timeout)
        except TimeoutError:
            await self._close()
            return
        try:
            message = parse_daemon(first)
        except InvalidMessage as exc:
            await self._refuse(exc.code, exc.detail)
            return
        if not isinstance(message, Register):
            await self._refuse("not_registered", "The first message must be register")
            return
        version = negotiate(message.protocol_versions, message.capabilities)
        if version is None:
            await self._refuse(
                "unsupported_protocol_version",
                f"The server speaks protocol versions {list(SERVER_PROTOCOL_VERSIONS)}",
            )
            return
        if not await self._register(message, version):
            await self._refuse("unknown_runner", "The token belongs to another runner")
            return
        registered = Registered(
            **self._envelope(message.correlation_id),
            runner_id=self.runner_id,
            protocol_version=version,
        )
        await self._send_text(registered.model_dump_json())
        await self._ack(message)
        await self._forward(("queued", "sent"))
        await self._run_until_closed()

    async def _run_until_closed(self) -> None:
        tasks = {
            asyncio.create_task(self._reader(), name="runner-reader"),
            asyncio.create_task(self._forwarder(), name="runner-forwarder"),
        }
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    _log.error("runner socket task failed", exc_info=exc)
        finally:
            # The forwarder stops at its next turn, never mid-query: a task cancelled while
            # it opens a database connection leaves a half-open login on the server.
            self.waiter._wake(close=True)
            forwarder = next(t for t in tasks if t.get_name() == "runner-forwarder")
            await asyncio.wait({forwarder}, timeout=STOP_S)
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self._close()

    # --- reading -------------------------------------------------------------------------

    async def _reader(self) -> None:
        invalid = 0
        while True:
            frame = await self.ws.receive_text()
            try:
                message = parse_daemon(frame)
            except InvalidMessage as exc:
                invalid += 1
                await self._error(exc.code, exc.detail)
                if invalid >= MAX_INVALID:
                    return
                continue
            invalid = 0
            if isinstance(message, Ack):
                await self._acked(message.ack_of)
                continue
            if await self._handle(message):
                await self._ack(message)

    async def _handle(self, message: DaemonMessage) -> bool:
        """Write the message (once per message_id) and act on it; True when it may be
        acked."""
        await self._store_inbound(message)
        if isinstance(message, Heartbeat):
            await self._heartbeat()
        elif isinstance(message, Register):
            version = negotiate(message.protocol_versions, message.capabilities)
            await self._register(message, version or 1)
        elif isinstance(message, Result):
            return await self._result(message)
        elif isinstance(message, HealthReport):
            await self._health_report(message)
        return True

    # --- forwarding ----------------------------------------------------------------------

    async def _forwarder(self) -> None:
        while True:
            self.waiter.event.clear()
            if self.waiter.close:
                return
            await self._forward(("queued",))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.waiter.event.wait(), POLL_S)

    async def _forward(self, statuses: tuple[str, ...]) -> None:
        """Send the runner's outbound mailbox rows in `statuses`, oldest first, then mark
        the queued ones sent (an ack may already have turned one `acked`)."""
        async with tenant_session(self.ctx) as s:
            rows = (
                await s.execute(
                    select(_messages.c.message_id, _messages.c.payload)
                    .where(
                        _messages.c.runner_id == self.runner_id,
                        _messages.c.direction == "out",
                        _messages.c.status.in_(statuses),
                        _messages.c.deleted_at.is_(None),
                    )
                    .order_by(_messages.c.created_at, _messages.c.id)
                )
            ).all()
        if not rows:
            return
        for row in rows:
            await self._send_text(json.dumps(row.payload))
        async with tenant_session(self.ctx) as s:
            await s.execute(
                update(_messages)
                .where(
                    _messages.c.message_id.in_([row.message_id for row in rows]),
                    _messages.c.status == "queued",
                )
                .values(status="sent", sent_at=self.now())
            )

    # --- writes --------------------------------------------------------------------------

    async def _register(self, message: Register, version: int) -> bool:
        """Update the runner from its register; False when the token's runner has another
        name (or is gone)."""
        async with tenant_session(self.ctx) as s:
            name = await s.scalar(
                select(_runners.c.name).where(
                    _runners.c.id == self.runner_id, _runners.c.deleted_at.is_(None)
                )
            )
            if name != message.runner_name:
                return False
            await s.execute(
                update(_runners)
                .where(_runners.c.id == self.runner_id)
                .values(
                    host=message.host,
                    os=message.os,
                    daemon_version=message.daemon_version,
                    hermes_version=message.hermes_version,
                    protocol_version=version,
                    inventory=[p.model_dump(mode="json") for p in message.profiles],
                    status="online",
                    last_heartbeat_at=self.now(),
                )
            )
            await self._insert_inbound(s, message)
            mark_changed(s, api.LIVE_RUNNER, self.runner_id)
        return True

    async def _insert_inbound(self, s: Any, message: DaemonMessage) -> None:
        await s.execute(
            insert(_messages)
            .values(
                runner_id=self.runner_id,
                message_id=message.message_id,
                direction="in",
                type=message.type,
                payload=message.model_dump(mode="json"),
                status="acked",
                acked_at=self.now(),
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
        )

    async def _store_inbound(self, message: DaemonMessage) -> None:
        if isinstance(message, Register):
            return  # _register writes it with the runner row
        async with tenant_session(self.ctx) as s:
            await self._insert_inbound(s, message)

    async def _acked(self, ack_of: UUID) -> None:
        async with tenant_session(self.ctx) as s:
            await s.execute(
                update(_messages)
                .where(
                    _messages.c.message_id == ack_of,
                    _messages.c.direction == "out",
                    _messages.c.runner_id == self.runner_id,
                )
                .values(status="acked", acked_at=self.now())
            )

    async def _heartbeat(self) -> None:
        async with tenant_session(self.ctx) as s:
            await s.execute(
                update(_runners)
                .where(_runners.c.id == self.runner_id)
                .values(status="online", last_heartbeat_at=self.now())
            )

    async def _result(self, message: Result) -> bool:
        """The result as a run event (once per message id), then handed to the run's
        workflow; acked only once the workflow has it. A result counts only from the runner
        the run was dispatched to (its `run` message, uuid5(run_id, "run")); from any other
        runner it is acked and dropped. The result also acks that `run` message: the runner
        got it even if its ack was lost, and a resent run would execute the skill again."""
        async with tenant_session(self.ctx) as s:
            dispatched_here = await s.scalar(
                update(_messages)
                .where(
                    _messages.c.message_id == uuid5(message.run_id, "run"),
                    _messages.c.direction == "out",
                    _messages.c.runner_id == self.runner_id,
                )
                .values(
                    status="acked",
                    acked_at=func.coalesce(_messages.c.acked_at, self.now()),
                )
                .returning(_messages.c.id)
            )
            if dispatched_here is None:
                _log.warning(
                    "dropped a result for a run not dispatched to this runner",
                    extra={"run": str(message.run_id), "runner": str(self.runner_id)},
                )
                return True
            await s.execute(
                insert(_events)
                .values(
                    run_id=message.run_id,
                    message_id=message.message_id,
                    kind="result",
                    payload=message.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
            )
            await s.execute(
                _NOTIFY,
                {
                    "channel": api.RUN_EVENTS_CHANNEL,
                    "payload": json.dumps({"run": str(message.run_id)}),
                },
            )
            workflow_id = await s.scalar(
                select(_runs.c.workflow_id).where(_runs.c.id == message.run_id)
            )
        if workflow_id is None:
            return True
        try:
            await self.hub.client().send_async(
                workflow_id,
                message.model_dump(mode="json"),
                api.run_topic(message.run_id),
                str(message.message_id),
            )
        except Exception:  # not acked: the daemon resends and the send is idempotent
            _log.exception("could not hand a result to its workflow")
            return False
        return True

    async def _health_report(self, message: HealthReport) -> None:
        try:
            await self.hub.client().send_async(
                api.health_workflow_id(message.request_id),
                message.model_dump(mode="json"),
                api.HEALTH_TOPIC,
                str(message.message_id),
            )
        except Exception:  # a report nobody waits for any more
            _log.warning("health report without its workflow", extra={"id": message.request_id})
