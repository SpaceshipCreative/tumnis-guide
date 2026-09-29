"""Event payloads, envelope, subscriber registry, the outbox relay and the delivery
workflow (P0-07, ADR-0011, ADR-0002)."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Literal, TypeVar
from uuid import UUID

import psycopg
from dbos import DBOS, SetEnqueueOptions, SetWorkflowID
from dbos._error import DBOSQueueDeduplicatedError  # dbos 3.1.0: not re-exported
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import text

from tumnis.core import db, faults, tenancy
from tumnis.core.schemas import VersionedPayload
from tumnis.core.types import SYSTEM_ACTOR

log = logging.getLogger(__name__)

EVENTS_QUEUE = "events"
EVENTS_WORKER_CONCURRENCY = 8  # A9
# How often the queue's worker thread looks for queued deliveries. DBOS's default (1 s)
# would put up to a second between the relay's enqueue and the handler.
EVENTS_QUEUE_POLL_S = 0.2
RELAY_BATCH = 100  # plan default
POLL_SECONDS = 5.0  # plan default ("polls every few seconds as a backstop")


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

    @classmethod
    def from_outbox_row(cls, row: Mapping[Any, Any]) -> "EventEnvelope":
        return cls(
            event_id=row["event_id"],
            name=row["name"],
            schema_version=row["schema_version"],
            workspace_id=row["workspace_id"],
            occurred_at=row["occurred_at"],
            actor=row["actor"],
            trace_context=row["trace_context"],
            payload=row["payload"],
        )

    def typed(self) -> EventPayload:
        """The payload validated with its registered model; EventSchemaError otherwise."""
        model = registry.model(self.name, self.schema_version)
        try:
            return model.model_validate(self.payload)
        except ValidationError as exc:
            raise EventSchemaError(f"{self.name} v{self.schema_version}: {exc}") from exc


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


# --- Relay: outbox rows -> one queued delivery workflow per subscriber ------------------

CLAIM = text("SELECT * FROM app.outbox_claim(:n)")
MARK_SENT = text("SELECT app.outbox_mark_sent(:ids)")


def delivery_id(event_id: UUID | str, subscriber: str) -> str:
    """The workflow ID (and deduplication ID) of one event's delivery to one subscriber."""
    return f"{event_id}:{subscriber}"


async def _enqueue(subscriber: str, envelope: EventEnvelope) -> None:
    """SetWorkflowID makes the enqueue idempotent for good (a second enqueue returns the
    existing workflow, even after it finished); the deduplication ID also refuses a
    duplicate that another workflow ID would hold while it is queued or running."""
    wf_id = delivery_id(envelope.event_id, subscriber)
    with (
        SetWorkflowID(wf_id),
        SetEnqueueOptions(deduplication_id=wf_id),
        contextlib.suppress(DBOSQueueDeduplicatedError),  # an earlier pass queued it
    ):
        await DBOS.enqueue_workflow_async(
            EVENTS_QUEUE, deliver_event, subscriber, envelope.model_dump(mode="json")
        )


async def relay_once(limit: int = RELAY_BATCH) -> int:
    """Claim up to `limit` unsent rows (app role, direct connection, no workspace), enqueue
    a delivery per subscriber, mark them sent, in one transaction. Returns the rows claimed."""
    async with db.direct_sessionmaker()() as session, session.begin():
        rows = (await session.execute(CLAIM, {"n": limit})).mappings().all()
        for row in rows:
            envelope = EventEnvelope.from_outbox_row(row)
            for sub in subscribers_for(envelope.name):
                await _enqueue(sub.name, envelope)
            faults.killpoint("relay.after_enqueue")
        if rows:
            await session.execute(MARK_SENT, {"ids": [row["id"] for row in rows]})
    faults.killpoint("relay.after_mark_sent")
    return len(rows)


async def relay_forever(stop: asyncio.Event, poll_s: float = POLL_SECONDS) -> None:
    """LISTEN outbox on its own direct connection; relay whenever a NOTIFY arrives, and at
    least every `poll_s` seconds as a backstop (a row committed without NOTIFY, or a
    notification lost while reconnecting). A backlog drains in batches before it sleeps.
    Errors are logged and retried after `poll_s`; cancel the task to stop it at once."""
    while not stop.is_set():
        try:
            await _listen_and_relay(stop, poll_s)
        except asyncio.CancelledError:
            raise
        except Exception:  # the supervisor loop: log, back off, reconnect
            log.exception("outbox relay failed; retrying in %s s", poll_s)
            await asyncio.sleep(poll_s)


async def _listen_and_relay(stop: asyncio.Event, poll_s: float) -> None:
    async with await psycopg.AsyncConnection.connect(db.direct_dsn(), autocommit=True) as conn:
        await conn.execute("LISTEN outbox")
        while not stop.is_set():
            while await relay_once() == RELAY_BATCH:
                pass  # drain a backlog before sleeping
            async for _ in conn.notifies(timeout=poll_s, stop_after=1):
                pass  # wake on NOTIFY or after poll_s


# --- Delivery: one workflow per (event, subscriber) -------------------------------------


@DBOS.workflow(name="deliver_event")
async def deliver_event(subscriber: str, envelope: dict[str, Any]) -> str:
    """Runs the subscriber's handler as a step. Deterministic between steps (ADR-0002)."""
    await run_handler(subscriber, envelope)
    faults.killpoint("deliver.after_handler")  # in the workflow body, after the step is recorded
    return "delivered"


@DBOS.step()
async def run_handler(subscriber: str, envelope: dict[str, Any]) -> None:
    sub, env = get_subscriber(subscriber), EventEnvelope.model_validate(envelope)
    with tenancy.use_workspace(tenancy.WorkspaceContext(env.workspace_id, SYSTEM_ACTOR)):
        await sub.handler(env)
