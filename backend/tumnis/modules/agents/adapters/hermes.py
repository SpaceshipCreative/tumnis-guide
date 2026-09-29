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

from collections.abc import AsyncIterator, Callable
from typing import Protocol
from uuid import UUID

import httpx

from tumnis.core.clock import Clock
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.agents.adapters.port import (
    AgentCapabilities,
    AgentHealth,
    RunEvent,
    RunHandle,
)
from tumnis.modules.agents.packet_builder import TaskPacket


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
    def __init__(self, ctx: WorkspaceContext, clock: Clock) -> None:
        self.ctx = ctx
        self.clock = clock

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
