"""HermesAgent (P1-04, FR-14.6, FR-5.11): the real AgentAdapter, one per profile, over one
of two transports.

- `DaemonTransport`: the runner daemon dialled in over `/ws/runner` (P1-04). The worker
  never touches the socket: `dispatch` writes the `runs` row, the `run` mailbox row and a
  `dispatched` run event, then NOTIFYs `runner_mailbox`; the api process holding the
  runner's socket forwards the row and writes the daemon's `result` as a run event.
  `stream` reads the run's events (LISTEN on the run events channel) up to the terminal
  one. P2-07: the `run` row is a version-2 `run` (the api renders it for a protocol-1
  daemon) whose `workdir_policy` asks for a worktree when the packet carries a code
  location; `cancel` queues a `cancel` for a protocol-2 runner and, for an older one,
  marks the run cancelled itself ("stop requested, runner is an older version").
- `McpEndpointTransport`: a profile kept running as a server, reached with the MCP Python
  SDK's Streamable HTTP client through `tumnis.core.net.guarded_client`; it calls the
  endpoint's `run_skill(profile, skill, packet_json)` tool. The pinned Hermes has no such
  tool (its `mcp serve` is a stdio bridge), so every real profile uses the daemon
  transport for now; this one is tested against an in-process fake MCP server.
"""

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Final, Protocol
from uuid import UUID, uuid5

import httpx
import psycopg
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from psycopg import sql
from sqlalchemy import Table, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import db
from tumnis.core.clock import Clock
from tumnis.core.net import NetPolicy, guarded_client
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.modules.agents.adapters.port import (
    AgentCapabilities,
    AgentHealth,
    AgentUnavailable,
    RunEvent,
    RunHandle,
)
from tumnis.modules.agents.models import AgentProfile, RunEventRow, Runner, RunnerMessage, RunRow
from tumnis.modules.agents.packet_builder import TaskPacket, workdir_policy
from tumnis.modules.agents.protocol import Cancel, HealthCheck, RunV2

# Channels (agents.api names them too; adapters may not import the api).
RUNNER_CHANNEL: Final = "runner_mailbox"
RUN_EVENTS_CHANNEL: Final = "agents_run_events"
PHASE_1_SKILLS: Final = frozenset({"enrich", "plan"})
STREAM_POLL_S: Final = 1.0
RUN_TOOL: Final = "run_skill"
MCP_GRACE_S: Final = 30  # the run's timeout plus this for the call
HEALTH_TIMEOUT_S: Final = 10.0
RESULT_STATUSES: Final = frozenset({"succeeded", "failed", "timed_out"})
_STREAMED: Final = frozenset(
    {"dispatched", "result", "failed", "log", "tool_call", "file", "artifact", "status"}
)
_TERMINAL: Final = frozenset({"result", "failed"})
TERMINAL_RUN: Final = frozenset({"succeeded", "failed", "cancelled", "timed_out", "runner_lost"})
OLDER_RUNNER: Final = "stop requested, runner is an older version"
PROTOCOL_2: Final = 2

_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_runners: Table = Runner.__table__  # type: ignore[assignment]
_messages: Table = RunnerMessage.__table__  # type: ignore[assignment]
_runs: Table = RunRow.__table__  # type: ignore[assignment]
_events: Table = RunEventRow.__table__  # type: ignore[assignment]
_NOTIFY = text("SELECT pg_notify(:channel, :payload)")


async def _notify(s: AsyncSession, channel: str, payload: dict[str, Any]) -> None:
    await s.execute(_NOTIFY, {"channel": channel, "payload": json.dumps(payload)})


def _workflow_id() -> str | None:
    """The DBOS workflow this dispatch runs in (the result is sent to it), None outside
    one."""
    try:
        from dbos import DBOS  # noqa: PLC0415

        workflow_id = DBOS.workflow_id
    except Exception:  # no DBOS in this process, or not inside a workflow
        return None
    return workflow_id


class AgentTransport(Protocol):
    def capabilities(self) -> AgentCapabilities: ...

    async def dispatch(self, packet: TaskPacket) -> RunHandle: ...

    def stream(self, run: RunHandle) -> AsyncIterator[RunEvent]: ...

    async def cancel(self, run: RunHandle) -> None: ...

    async def health(self, profile_id: UUID) -> AgentHealth: ...


class HermesAgent:
    """The AgentAdapter for one Hermes profile, over its transport."""

    def __init__(self, profile_id: UUID, transport: AgentTransport) -> None:
        self.profile_id = profile_id
        self.transport = transport

    def capabilities(self) -> AgentCapabilities:
        return self.transport.capabilities()

    async def dispatch(self, packet: TaskPacket) -> RunHandle:
        return await self.transport.dispatch(packet)

    def stream(self, run: RunHandle) -> AsyncIterator[RunEvent]:
        return self.transport.stream(run)

    async def cancel(self, run: RunHandle) -> None:
        await self.transport.cancel(run)

    async def health(self) -> AgentHealth:
        return await self.transport.health(self.profile_id)


class DaemonTransport:
    """The worker's side of the runner mailbox: rows and NOTIFYs, never the socket."""

    def __init__(self, ctx: WorkspaceContext, clock: Clock) -> None:
        self.ctx = ctx
        self.clock = clock

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            transport="daemon",
            skills=PHASE_1_SKILLS,
            supports_stream=False,
            supports_cancel=False,
        )

    async def _runner_for(self, s: AsyncSession, profile_id: UUID) -> tuple[str, UUID]:
        """The profile's name and its runner, when the runner is online and its last
        register listed the profile; AgentUnavailable otherwise. The worker judges by the
        runner's stored status (the sweep keeps it), not by comparing clocks."""
        profile = (
            await s.execute(
                select(_profiles.c.name, _profiles.c.runner_id, _profiles.c.status).where(
                    _profiles.c.id == profile_id, _profiles.c.deleted_at.is_(None)
                )
            )
        ).first()
        if profile is None:
            raise AgentUnavailable(profile_id, "unknown profile")
        if profile.status == "paused" or profile.runner_id is None:
            raise AgentUnavailable(profile_id, "paused or without a runner")
        runner = (
            await s.execute(
                select(_runners.c.status, _runners.c.inventory).where(
                    _runners.c.id == profile.runner_id, _runners.c.deleted_at.is_(None)
                )
            )
        ).first()
        if runner is None or runner.status != "online":
            raise AgentUnavailable(profile_id, "runner offline")
        if profile.name not in {str(p.get("name")) for p in runner.inventory}:
            raise AgentUnavailable(profile_id, "profile not on its runner")
        return str(profile.name), profile.runner_id

    async def dispatch(self, packet: TaskPacket) -> RunHandle:
        """In one transaction: the `runs` row, the `run` mailbox row (message id
        uuid5(run_id, "run"), so a replayed step queues nothing new), the `dispatched` run
        event and the NOTIFY that wakes the runner's socket."""
        now = self.clock.now()
        async with tenant_session(self.ctx) as s:
            profile, runner_id = await self._runner_for(s, packet.profile_id)
            await s.execute(
                insert(_runs)
                .values(
                    id=packet.run_id,
                    profile_id=packet.profile_id,
                    kind=packet.kind.value,
                    status="running",
                    workflow_id=_workflow_id(),
                    started_at=now,
                    correlation_id=packet.correlation_id,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            run = RunV2(
                message_id=uuid5(packet.run_id, "run"),
                correlation_id=packet.correlation_id,
                sent_at=now,
                run_id=packet.run_id,
                profile=profile,
                skill=packet.skill,
                packet=packet.model_dump(mode="json"),
                output_schema=packet.output_schema,
                timeout_s=packet.timeout_s,
                workdir_policy=workdir_policy(packet),
            )
            await s.execute(
                insert(_messages)
                .values(
                    runner_id=runner_id,
                    message_id=run.message_id,
                    direction="out",
                    type=run.type,
                    payload=run.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
            )
            await s.execute(
                insert(_events)
                .values(
                    run_id=packet.run_id,
                    message_id=uuid5(packet.run_id, "dispatched"),
                    kind="dispatched",
                    payload={
                        "profile": profile,
                        "skill": packet.skill,
                        "runner_id": str(runner_id),
                    },
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
            )
            await _notify(s, RUNNER_CHANNEL, {"runner": str(runner_id), "close": False})
            await _notify(s, RUN_EVENTS_CHANNEL, {"run": str(packet.run_id)})
        return RunHandle(
            run_id=packet.run_id,
            profile_id=packet.profile_id,
            transport="daemon",
            correlation_id=packet.correlation_id,
        )

    async def _events_after(self, run_id: UUID, seen: set[UUID]) -> list[RunEvent]:
        async with tenant_session(self.ctx) as s:
            rows = (
                await s.execute(
                    select(_events)
                    .where(_events.c.run_id == run_id, _events.c.deleted_at.is_(None))
                    .order_by(_events.c.created_at, _events.c.id)
                )
            ).mappings()
            found = [
                RunEvent(
                    run_id=row["run_id"],
                    message_id=row["message_id"],
                    kind=row["kind"],
                    payload=row["payload"],
                    at=row["created_at"],
                )
                for row in rows
                if row["message_id"] not in seen and row["kind"] in _STREAMED
            ]
        seen.update(event.message_id for event in found)
        return found

    async def stream(self, run: RunHandle) -> AsyncIterator[RunEvent]:
        """The run's events up to its terminal one (`result` or `failed`): LISTEN on the
        run events channel, with a poll as a backstop; replayed for a finished run."""
        seen: set[UUID] = set()
        async with await psycopg.AsyncConnection.connect(db.direct_dsn(), autocommit=True) as conn:
            await conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(RUN_EVENTS_CHANNEL)))
            while True:
                for event in await self._events_after(run.run_id, seen):
                    yield event
                    if event.kind in _TERMINAL:
                        return
                async for _notice in conn.notifies(timeout=STREAM_POLL_S, stop_after=1):
                    pass

    async def cancel(self, run: RunHandle) -> None:
        """Stop a run. A finished run is left as it is. A runner on protocol 2 gets a
        `cancel` (mailbox row uuid5(run_id, "cancel")) and answers with a `cancelled`
        result. An older runner would not understand one: the run is marked cancelled
        here, with a `failed` run event, its workflow is told, and the result the runner
        sends when it ends is acked and ignored (REL-4)."""
        now = self.clock.now()
        async with tenant_session(self.ctx) as s:
            found = (
                await s.execute(
                    select(_runs.c.status, _runs.c.workflow_id).where(_runs.c.id == run.run_id)
                )
            ).first()
            if found is None or found.status in TERMINAL_RUN:
                return
            ended = await s.scalar(
                select(_events.c.id).where(
                    _events.c.run_id == run.run_id, _events.c.kind.in_(_TERMINAL)
                )
            )
            if ended is not None:
                return
            mailbox = (
                await s.execute(
                    select(_messages.c.runner_id, _runners.c.protocol_version)
                    .join(_runners, _runners.c.id == _messages.c.runner_id)
                    .where(_messages.c.message_id == uuid5(run.run_id, "run"))
                )
            ).first()
            if mailbox is not None and (mailbox.protocol_version or 1) >= PROTOCOL_2:
                cancel = Cancel(
                    message_id=uuid5(run.run_id, "cancel"),
                    correlation_id=run.correlation_id,
                    sent_at=now,
                    run_id=run.run_id,
                    reason="stopped by the user",
                )
                await s.execute(
                    insert(_messages)
                    .values(
                        runner_id=mailbox.runner_id,
                        message_id=cancel.message_id,
                        direction="out",
                        type=cancel.type,
                        payload=cancel.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
                )
                await _notify(s, RUNNER_CHANNEL, {"runner": str(mailbox.runner_id), "close": False})
                return
            await s.execute(
                update(_runs)
                .where(_runs.c.id == run.run_id)
                .values(status="cancelled", finished_at=now, error=OLDER_RUNNER)
            )
            await s.execute(
                insert(_events)
                .values(
                    run_id=run.run_id,
                    message_id=uuid5(run.run_id, "failed"),
                    kind="failed",
                    payload={"status": "cancelled", "error": OLDER_RUNNER},
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
            )
            await _notify(s, RUN_EVENTS_CHANNEL, {"run": str(run.run_id)})
            workflow_id = found.workflow_id
        if workflow_id is not None:
            await _tell_workflow(workflow_id, run.run_id)

    async def health(self, profile_id: UUID) -> AgentHealth:
        async with tenant_session(self.ctx) as s:
            try:
                await self._runner_for(s, profile_id)
            except AgentUnavailable as exc:
                return AgentHealth(status="offline", reachable=False, detail=exc.reason)
        return AgentHealth(status="ok", reachable=True)

    async def request_health(self, profile_id: UUID, request_id: UUID) -> None:
        """Queue a `health_check` for the profile on its runner (message id
        uuid5(request_id, "health_check"), so a replayed step queues it once); the runner's
        `health_report` reaches workflow `profile-health:<request_id>`. AgentUnavailable
        when the runner is offline or does not list the profile."""
        async with tenant_session(self.ctx) as s:
            profile, runner_id = await self._runner_for(s, profile_id)
            check = HealthCheck(
                message_id=uuid5(request_id, "health_check"),
                correlation_id=f"req:{request_id}",
                sent_at=self.clock.now(),
                request_id=request_id,
                profile=profile,
            )
            await s.execute(
                insert(_messages)
                .values(
                    runner_id=runner_id,
                    message_id=check.message_id,
                    direction="out",
                    type=check.type,
                    payload=check.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
            )
            await _notify(s, RUNNER_CHANNEL, {"runner": str(runner_id), "close": False})


async def _tell_workflow(workflow_id: str, run_id: UUID) -> None:
    """Best effort: the run's waiting `run_skill` workflow hears it was cancelled (the
    topic agents.api.run_topic names; adapters may not import the api)."""
    try:
        from dbos import DBOS  # noqa: PLC0415

        await DBOS.send_async(
            workflow_id,
            {"status": "cancelled", "error": OLDER_RUNNER},
            f"run:{run_id}",
            idempotency_key=f"cancel:{run_id}",
        )
    except Exception:  # no DBOS here, or the workflow is gone: the run row says it all
        return


def _unreachable(exc: BaseException) -> bool:
    """A refused or failed connection, possibly inside the SDK's task-group exception
    groups."""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, OSError)):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_unreachable(inner) for inner in exc.exceptions)
    return False


class McpEndpointTransport:
    """A profile kept running as a server: the endpoint's `run_skill(profile, skill,
    packet_json)` tool, over the MCP SDK's Streamable HTTP client through the SSRF-guarded
    httpx client. The call returns when the run ends, so the run's events are kept here."""

    def __init__(
        self,
        endpoint: str,
        *,
        profile: str,
        clock: Clock,
        token: str | None = None,
        net_policy: NetPolicy | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.profile = profile
        self.clock = clock
        self.token = token
        self.net_policy = net_policy
        self.client_factory = client_factory
        self._events: dict[UUID, list[RunEvent]] = {}

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            transport="mcp_endpoint",
            skills=PHASE_1_SKILLS,
            supports_stream=False,
            supports_cancel=False,
        )

    def _client(self, timeout_s: float) -> httpx.AsyncClient:
        if self.client_factory is not None:
            client = self.client_factory()
        else:
            policy = self.net_policy
            if policy is None:
                from tumnis.settings import Settings  # noqa: PLC0415

                policy = Settings().net_policy()
            client = guarded_client(policy, timeout=timeout_s)
        if self.token:
            client.headers["Authorization"] = f"Bearer {self.token}"
        return client

    @asynccontextmanager
    async def _session(self, timeout_s: float) -> AsyncIterator[ClientSession]:
        async with (
            self._client(timeout_s) as client,
            streamable_http_client(self.endpoint, http_client=client) as (read, write, *_),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    async def dispatch(self, packet: TaskPacket) -> RunHandle:
        started = self.clock.now()
        arguments = {
            "profile": self.profile,
            "skill": packet.skill,
            "packet_json": packet.model_dump_json(),
        }
        try:
            async with self._session(packet.timeout_s + MCP_GRACE_S) as session:
                answer = await session.call_tool(RUN_TOOL, arguments)
        except Exception as exc:
            if _unreachable(exc):
                raise AgentUnavailable(packet.profile_id, "endpoint unreachable") from None
            raise
        result = _tool_result(answer)
        self._events[packet.run_id] = [
            RunEvent(
                run_id=packet.run_id,
                message_id=uuid5(packet.run_id, "dispatched"),
                kind="dispatched",
                payload={"profile": self.profile, "skill": packet.skill},
                at=started,
            ),
            RunEvent(
                run_id=packet.run_id,
                message_id=uuid5(packet.run_id, "result"),
                kind="result",
                payload=result,
                at=self.clock.now(),
            ),
        ]
        return RunHandle(
            run_id=packet.run_id,
            profile_id=packet.profile_id,
            transport="mcp_endpoint",
            correlation_id=packet.correlation_id,
        )

    async def stream(self, run: RunHandle) -> AsyncIterator[RunEvent]:
        for event in self._events.get(run.run_id, []):
            yield event

    async def cancel(self, run: RunHandle) -> None:
        """Phase 1 has no agent-side cancel; the call has already returned."""

    async def health(self, profile_id: UUID) -> AgentHealth:
        """`ok` when the endpoint lists the run tool, `unsupported` when it answers without
        it (the pinned Hermes `mcp serve`), `offline` when it does not answer."""
        try:
            async with self._session(HEALTH_TIMEOUT_S) as session:
                tools = await session.list_tools()
        except Exception as exc:
            if _unreachable(exc):
                return AgentHealth(status="offline", reachable=False, detail="endpoint unreachable")
            raise
        if RUN_TOOL in {tool.name for tool in tools.tools}:
            return AgentHealth(status="ok", reachable=True)
        return AgentHealth(
            status="unsupported", reachable=True, detail=f"no {RUN_TOOL} tool on the endpoint"
        )


def _tool_result(answer: Any) -> dict[str, Any]:
    """The run's result from the tool's JSON text: `{status, output_json, text, error,
    duration_ms}`; a tool error or text that is not that is a failed run."""
    texts = [getattr(part, "text", "") for part in answer.content]
    if answer.isError or not texts:
        return {"status": "failed", "output_json": None, "error": " ".join(texts) or "tool_error"}
    try:
        body = json.loads(texts[0])
    except ValueError:
        return {"status": "failed", "output_json": None, "error": "no_json"}
    if not isinstance(body, dict) or body.get("status") not in RESULT_STATUSES:
        return {"status": "failed", "output_json": None, "error": "invalid_result"}
    output = body.get("output_json")
    return {
        "status": body["status"],
        "output_json": output if isinstance(output, dict) else None,
        "error": body.get("error"),
        "text": body.get("text", ""),
        "duration_ms": body.get("duration_ms"),
    }
