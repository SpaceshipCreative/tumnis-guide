"""FakeAgent (P1-04): the in-memory AgentAdapter for unit and Playwright runs, no socket.

Scripted per skill with `script(skill, output_json, status=..., delay_ms=...)`; an
unscripted skill answers `{}`. `offline()` makes every dispatch raise AgentUnavailable
until `online()`. `calls` records each dispatched packet. In the compose.test stack it is
scripted through `POST /v1/test/fakes/runner/script`.
"""

from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import UUID

from tumnis.modules.agents.adapters.port import (
    AgentCapabilities,
    AgentHealth,
    RunEvent,
    RunHandle,
)
from tumnis.modules.agents.packet_builder import TaskPacket

ScriptedStatus = Literal["succeeded", "failed", "timed_out"]


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[TaskPacket] = []

    def script(
        self,
        skill: str,
        output_json: dict[str, Any] | None,
        *,
        status: ScriptedStatus = "succeeded",
        delay_ms: int = 0,
    ) -> None:
        raise NotImplementedError(f"P1-04 {skill} {output_json} {status} {delay_ms}")

    def offline(self, profile_id: UUID | None = None) -> None:
        raise NotImplementedError(f"P1-04 {profile_id}")

    def online(self) -> None:
        raise NotImplementedError("P1-04")

    def capabilities(self) -> AgentCapabilities:
        raise NotImplementedError("P1-04")

    async def dispatch(self, packet: TaskPacket) -> RunHandle:
        raise NotImplementedError(f"P1-04 {packet}")

    async def stream(self, run: RunHandle) -> AsyncIterator[RunEvent]:
        raise NotImplementedError(f"P1-04 {run}")
        yield  # pragma: no cover

    async def cancel(self, run: RunHandle) -> None:
        raise NotImplementedError(f"P1-04 {run}")

    async def health(self) -> AgentHealth:
        raise NotImplementedError("P1-04")
