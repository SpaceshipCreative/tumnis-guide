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


class EventSchemaError(ValueError):
    """The payload's (name, version) is not registered, or the payload fails its model."""


class EventPayload(VersionedPayload):
    """Base for event payloads. Each subclass declares `schema_version: Literal[<n>] = <n>`
    as a model field, so the value travels in the payload and its JSON Schema carries a
    const."""

    event_name: ClassVar[str]


P = TypeVar("P", bound=EventPayload)


class EventTypes:
    """(name, version) -> payload model. Until P0-11 merges this is its own index; P0-11
    records each entry in the `events` schema family through `versioned(...)` without
    changing callers."""

    def __init__(self) -> None:
        self._models: dict[tuple[str, int], type[EventPayload]] = {}

    def register(self, name: str, version: int, model: type[EventPayload]) -> None:
        existing = self._models.get((name, version))
        if existing is not None:
            raise ValueError(
                f"event type {name} v{version} is already registered by {existing.__qualname__}"
            )
        self._models[name, version] = model

    def model(self, name: str, version: int) -> type[EventPayload]:
        try:
            return self._models[name, version]
        except KeyError:
            raise EventSchemaError(f"unknown event type {name} v{version}") from None


registry = EventTypes()


def event_type(name: str, version: int) -> Callable[[type[P]], type[P]]:
    """Registers a payload model under (name, version), which must be unique. The model's
    `event_name` and `schema_version` default must say the same."""

    def register(model: type[P]) -> type[P]:
        declared = model.model_fields["schema_version"].default
        if getattr(model, "event_name", None) != name or declared != version:
            raise ValueError(
                f"{model.__qualname__} declares {getattr(model, 'event_name', None)} "
                f"v{declared}, registered as {name} v{version}"
            )
        registry.register(name, version, model)
        return model

    return register


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


_subscribers: dict[str, Subscriber] = {}


def subscribe(
    event: str,
    *,
    name: str,
    max_attempts: int = 5,
    base_delay_s: float = 2.0,
    cap_s: float = 300.0,
) -> Callable[[Handler], Handler]:
    """Registers `handler` as subscriber `name` ("<module>.<handler>") of `event`. The name
    is part of every delivery's workflow ID: never rename a subscriber (add a new one and
    delete the old). Handlers must be idempotent: a crash inside one re-runs it."""
    module, dot, handler_name = name.partition(".")
    if not (module and dot and handler_name):
        raise ValueError(f"subscriber name {name!r} is not '<module>.<handler>'")
    if max_attempts < 1:
        raise ValueError(f"subscriber {name}: max_attempts must be at least 1")

    def register(handler: Handler) -> Handler:
        if name in _subscribers:
            raise ValueError(f"subscriber {name} is already registered")
        _subscribers[name] = Subscriber(
            name, module, event, handler, max_attempts, base_delay_s, cap_s
        )
        return handler

    return register


def subscribers_for(event: str) -> tuple[Subscriber, ...]:
    return tuple(sorted((s for s in _subscribers.values() if s.event == event), key=_by_name))


def get_subscriber(name: str) -> Subscriber:
    try:
        return _subscribers[name]
    except KeyError:
        raise LookupError(f"no subscriber named {name}") from None


def _by_name(sub: Subscriber) -> str:
    return sub.name


async def relay_once(limit: int = RELAY_BATCH) -> int:
    raise NotImplementedError("P0-07")


async def relay_forever(stop: asyncio.Event, poll_s: float = POLL_SECONDS) -> None:
    raise NotImplementedError("P0-07")
