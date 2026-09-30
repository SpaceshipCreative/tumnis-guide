"""FakeAgent (P1-04): the in-memory AgentAdapter for unit and Playwright runs, no socket.

Scripted per skill with `script(skill, output_json, status=..., delay_ms=...)`; an
unscripted skill answers `{}`. `offline()` makes every dispatch raise AgentUnavailable
until `online()`. `calls` records each dispatched packet. In the compose.test stack it is
scripted through `POST /v1/test/fakes/runner/script`.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid5

from tumnis.core.clock import Clock, SystemClock
from tumnis.modules.agents.adapters.port import (
    AgentCapabilities,
    AgentHealth,
    AgentUnavailable,
    RunEvent,
    RunHandle,
)
from tumnis.modules.agents.packet_builder import TaskPacket

ScriptedStatus = Literal["succeeded", "failed", "timed_out"]


@dataclass(frozen=True)
class _Script:
    output_json: dict[str, Any] | None
    status: ScriptedStatus
    delay_ms: int


class FakeAgent:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock: Clock = clock or SystemClock()
        self.calls: list[TaskPacket] = []
        self._scripts: dict[str, _Script] = {}
        self._offline_all = False
        self._offline: set[UUID] = set()
        self._events: dict[UUID, list[RunEvent]] = {}

    def script(
        self,
        skill: str,
        output_json: dict[str, Any] | None,
        *,
        status: ScriptedStatus = "succeeded",
        delay_ms: int = 0,
    ) -> None:
        """What a run of `skill` answers from now on."""
        self._scripts[skill] = _Script(output_json, status, delay_ms)

    def offline(self, profile_id: UUID | None = None) -> None:
        """Dispatch to `profile_id` (every profile when None) raises AgentUnavailable."""
        if profile_id is None:
            self._offline_all = True
        else:
            self._offline.add(profile_id)

    def online(self) -> None:
        self._offline_all = False
        self._offline.clear()

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            transport="fake",
            skills=frozenset({"enrich", "plan"}),
            supports_stream=False,
            supports_cancel=False,
        )

    async def dispatch(self, packet: TaskPacket) -> RunHandle:
        if self._offline_all or packet.profile_id in self._offline:
            raise AgentUnavailable(packet.profile_id, "offline")
        self.calls.append(packet)
        script = self._scripts.get(packet.skill, _Script({}, "succeeded", 0))
        if script.delay_ms:
            await asyncio.sleep(script.delay_ms / 1000)
        now = self.clock.now()
        result = {
            "status": script.status,
            "output_json": script.output_json,
            "error": None if script.status == "succeeded" else f"scripted {script.status}",
        }
        self._events[packet.run_id] = [
            RunEvent(
                run_id=packet.run_id,
                message_id=uuid5(packet.run_id, "dispatched"),
                kind="dispatched",
                payload={"skill": packet.skill},
                at=now,
            ),
            RunEvent(
                run_id=packet.run_id,
                message_id=uuid5(packet.run_id, "result"),
                kind="result",
                payload=result,
                at=now,
            ),
        ]
        return RunHandle(
            run_id=packet.run_id,
            profile_id=packet.profile_id,
            transport="fake",
            correlation_id=packet.correlation_id,
        )

    async def stream(self, run: RunHandle) -> AsyncIterator[RunEvent]:
        """The run's events: `dispatched`, then its one `result`; replayed for a finished
        run."""
        for event in self._events.get(run.run_id, []):
            yield event

    async def cancel(self, run: RunHandle) -> None:
        """Phase 1 has no cancel on the agent side; a finished run stays finished."""

    async def health(self) -> AgentHealth:
        return AgentHealth(status="ok", reachable=True, authenticated=True, version="fake")
