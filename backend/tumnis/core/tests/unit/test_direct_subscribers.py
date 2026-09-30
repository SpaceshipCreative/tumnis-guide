"""Direct subscribers (P1-07, FR-3.3): the relay runs a `direct=True` handler itself, as
it reads the outbox, instead of queueing its delivery; a handler that raises falls back to
the queued delivery, and never fails the relay's pass. Pure: the relay's session, the
module switch and the queueing are stand-ins."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

EVENT = "test.direct"
WORKSPACE = uuid.UUID("01a0f170-0000-7000-8000-000000000001")


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Session:
    """The relay's session: the claim returns `rows`; the mark-sent call is recorded."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.marked: list[Any] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        if "ids" in params:
            self.marked.append(params["ids"])
            return _Result([])
        return _Result(self.rows)

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _row() -> dict[str, Any]:
    return {
        "id": 1,
        "event_id": uuid.uuid4(),
        "name": EVENT,
        "schema_version": 1,
        "workspace_id": WORKSPACE,
        "occurred_at": datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
        "actor": "system",
        "trace_context": {},
        "payload": {},
    }


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Registers three `test.direct` subscribers (direct and fine, direct and failing,
    queued), stands in for the session, the module switch and the queueing, and removes
    the subscribers afterwards."""
    from tumnis.core import db, events, modules  # noqa: PLC0415

    seen: dict[str, Any] = {"ran": [], "queued": [], "session": _Session([_row()])}

    async def fine(envelope: Any) -> None:
        seen["ran"].append(("testdirect.fine", envelope.event_id))

    async def failing(envelope: Any) -> None:
        seen["ran"].append(("testdirect.failing", envelope.event_id))
        raise RuntimeError("cannot start")

    async def queued(envelope: Any) -> None:  # pragma: no cover  # only ever queued here
        raise AssertionError("a queued subscriber's handler runs in its delivery")

    events.subscribe(EVENT, name="testdirect.fine", direct=True)(fine)
    events.subscribe(EVENT, name="testdirect.failing", direct=True)(failing)
    events.subscribe(EVENT, name="testdirect.queued")(queued)

    async def enabled(module: str, workspace_id: uuid.UUID) -> bool:
        return True

    async def enqueue(subscriber: str, envelope: Any) -> None:
        seen["queued"].append(subscriber)

    monkeypatch.setattr(modules, "enabled", enabled)
    monkeypatch.setattr(events, "_enqueue", enqueue)
    monkeypatch.setattr(db, "direct_sessionmaker", lambda: lambda: seen["session"])
    try:
        yield seen
    finally:
        for name in ("testdirect.fine", "testdirect.failing", "testdirect.queued"):
            events._subscribers.pop(name, None)


@pytest.mark.req("FR-3.3")
@pytest.mark.wp("P1-07")
def test_subscribe_records_direct() -> None:
    from tumnis.core import events  # noqa: PLC0415

    async def handler(envelope: Any) -> None:  # pragma: no cover  # never called
        return None

    events.subscribe(EVENT, name="testdirect.flag", direct=True)(handler)
    try:
        assert events.get_subscriber("testdirect.flag").direct is True
        assert events.get_subscriber("testdirect.flag").max_attempts == 5
    finally:
        events._subscribers.pop("testdirect.flag", None)


@pytest.mark.req("FR-3.3")
@pytest.mark.wp("P1-07")
async def test_relay_runs_direct_subscribers_and_queues_the_rest(relay: dict[str, Any]) -> None:
    """A direct handler runs in the relay's pass and is not queued; one that raises is
    queued as an ordinary delivery; a plain subscriber is queued; the row is marked sent."""
    from tumnis.core.events import relay_once  # noqa: PLC0415

    claimed = await relay_once()

    assert claimed == 1
    assert [name for name, _ in relay["ran"]] == ["testdirect.failing", "testdirect.fine"]
    assert relay["queued"] == ["testdirect.failing", "testdirect.queued"]
    assert relay["session"].marked == [[1]]
