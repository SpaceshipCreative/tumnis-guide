"""Event payloads, envelope, subscriber registry, the outbox relay and the delivery
workflow (P0-07, ADR-0011, ADR-0002)."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from tumnis.core.schemas import VersionedPayload

EVENTS_QUEUE = "events"
RELAY_BATCH = 100  # plan default
POLL_SECONDS = 5.0  # plan default


class EventPayload(VersionedPayload):
    """Base for event payloads."""

    event_name: ClassVar[str]


P = TypeVar("P", bound=EventPayload)


class _Registry:
    def model(self, name: str, version: int) -> type[EventPayload]:
        raise NotImplementedError("P0-07")


registry = _Registry()


def event_type(name: str, version: int) -> Callable[[type[P]], type[P]]:
    raise NotImplementedError("P0-07")


@event_type("test.ping", 1)  # example; real events arrive with their modules
class TestPingV1(EventPayload):
    event_name: ClassVar[str] = "test.ping"
    schema_version: Literal[1] = 1
    note: str


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: UUID
    name: str
    schema_version: int
    workspace_id: UUID
    occurred_at: datetime
    actor: str
    trace_context: dict[str, str] = {}
    payload: dict[str, Any]


Handler = Callable[[EventEnvelope], Awaitable[None]]


@dataclass(frozen=True)
class Subscriber:
    name: str
    module: str
    event: str
    handler: Handler
    max_attempts: int = 5
    base_delay_s: float = 2.0
    cap_s: float = 300.0


def subscribe(
    event: str,
    *,
    name: str,
    max_attempts: int = 5,
    base_delay_s: float = 2.0,
    cap_s: float = 300.0,
) -> Callable[[Handler], Handler]:
    raise NotImplementedError("P0-07")


def subscribers_for(event: str) -> tuple[Subscriber, ...]:
    raise NotImplementedError("P0-07")


async def relay_once(limit: int = RELAY_BATCH) -> int:
    raise NotImplementedError("P0-07")


async def relay_forever(stop: asyncio.Event, poll_s: float = POLL_SECONDS) -> None:
    raise NotImplementedError("P0-07")
