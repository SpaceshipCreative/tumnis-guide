"""HermesAgent (P1-04, FR-14.6, FR-5.11): the real AgentAdapter, one per profile, over one
of two transports.

- `DaemonTransport`: the runner daemon dialled in over `/ws/runner` (P1-04). The worker
  never touches the socket: `dispatch` writes the `runs` row, the `run` mailbox row and a
  `dispatched` run event, then NOTIFYs `runner_mailbox`; the api process holding the
  runner's socket forwards the row and writes the daemon's `result` as a run event.
  `stream` reads the run's events (LISTEN on the run events channel) up to the terminal
  one.
- `McpEndpointTransport`: a profile kept running as a server, reached with the MCP Python
  SDK's Streamable HTTP client through `tumnis.core.net.guarded_client`; it calls the
  endpoint's `run_skill(profile, skill, packet_json)` tool. The pinned Hermes has no such
  tool (its `mcp serve` is a stdio bridge), so every real profile uses the daemon
  transport for now; this one is tested against an in-process fake MCP server.
"""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Final, Protocol
from uuid import UUID, uuid5

import httpx
import psycopg
from sqlalchemy import Table, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import db
from tumnis.core.clock import Clock
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.modules.agents.adapters.port import (
    AgentCapabilities,
    AgentHealth,
    AgentUnavailable,
    RunEvent,
    RunHandle,
)
from tumnis.modules.agents.models import AgentProfile, RunEventRow, Runner, RunnerMessage, RunRow
from tumnis.modules.agents.packet_builder import TaskPacket
from tumnis.modules.agents.protocol import HealthCheck, Run

# Channels (agents.api names them too; adapters may not import the api).
RUNNER_CHANNEL: Final = "runner_mailbox"
RUN_EVENTS_CHANNEL: Final = "agents_run_events"
PHASE_1_SKILLS: Final = frozenset({"enrich", "plan"})
STREAM_POLL_S: Final = 1.0
_STREAMED: Final = frozenset({"dispatched", "result", "failed"})
_TERMINAL: Final = frozenset({"result", "failed"})

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
            run = Run(
                message_id=uuid5(packet.run_id, "run"),
                correlation_id=packet.correlation_id,
                sent_at=now,
                run_id=packet.run_id,
                profile=profile,
                skill=packet.skill,
                packet=packet.model_dump(mode="json"),
                output_schema=packet.output_schema,
                timeout_s=packet.timeout_s,
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
            await conn.execute(f"LISTEN {RUN_EVENTS_CHANNEL}")
            while True:
                for event in await self._events_after(run.run_id, seen):
                    yield event
                    if event.kind in _TERMINAL:
                        return
                async for _notice in conn.notifies(timeout=STREAM_POLL_S, stop_after=1):
                    pass

    async def cancel(self, run: RunHandle) -> None:
        """Phase 1 has no agent-side cancel (protocol 2 adds it); a finished run is left
        as it is."""

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


class McpEndpointTransport:
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

    def capabilities(self) -> AgentCapabilities:
        raise NotImplementedError("P1-04")

    async def dispatch(self, packet: TaskPacket) -> RunHandle:
        raise NotImplementedError(f"P1-04 {packet}")

    async def stream(self, run: RunHandle) -> AsyncIterator[RunEvent]:
        raise NotImplementedError(f"P1-04 {run}")
        yield  # pragma: no cover

    async def cancel(self, run: RunHandle) -> None:
        raise NotImplementedError(f"P1-04 {run}")

    async def health(self, profile_id: UUID) -> AgentHealth:
        raise NotImplementedError(f"P1-04 {profile_id}")
