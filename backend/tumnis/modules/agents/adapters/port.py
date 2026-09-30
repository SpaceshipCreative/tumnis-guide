"""The AgentAdapter port (P1-04, FR-14.6): the one interface the worker sends a packet
through and reads the run back from, whatever carries it (the runner daemon over
`/ws/runner`, a remote MCP endpoint, or the in-memory fake). `agents.api` re-exports it.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from tumnis.modules.agents.packet_builder import TaskPacket

Transport = Literal["daemon", "mcp_endpoint", "fake"]


class AgentUnavailable(Exception):  # noqa: N818  # the plan's name
    """The profile cannot take a run now: its runner is offline, it is missing from the
    runner's inventory, or its endpoint does not answer. Nothing was dispatched."""

    code = "agent_offline"

    def __init__(self, profile_id: UUID, reason: str) -> None:
        super().__init__(f"agent profile {profile_id} unavailable: {reason}")
        self.profile_id = profile_id
        self.reason = reason


class AgentCapabilities(BaseModel):
    transport: Transport
    skills: frozenset[str]
    supports_stream: bool  # False in phase 1
    supports_cancel: bool  # False in phase 1 (server-side cancel only)


class RunHandle(BaseModel):
    run_id: UUID
    profile_id: UUID
    transport: str
    correlation_id: str


class RunEvent(BaseModel):
    run_id: UUID
    message_id: UUID
    # P2-07 adds the protocol-2 run events: stream lines, statuses and artifacts.
    kind: Literal[
        "dispatched", "result", "failed", "log", "tool_call", "file", "artifact", "status"
    ]
    payload: dict[str, Any]
    at: datetime


class AgentHealth(BaseModel):
    """What a profile's transport reports. `status` is `ok` (reachable), `offline` or
    `unsupported` (the pinned Hermes has no run endpoint for the MCP transport)."""

    status: Literal["ok", "offline", "unsupported"]
    reachable: bool
    authenticated: bool | None = None
    version: str | None = None
    detail: str | None = None


class AgentAdapter(Protocol):
    def capabilities(self) -> AgentCapabilities: ...

    async def dispatch(self, packet: TaskPacket) -> RunHandle: ...

    def stream(self, run: RunHandle) -> AsyncIterator[RunEvent]: ...

    async def cancel(self, run: RunHandle) -> None: ...

    async def health(self) -> AgentHealth: ...
