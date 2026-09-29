"""Test subscribers and the `test_deliveries` scratch table (P0-07).

Imported by the relay and dead-letter tests, and by the subprocess worker through
`python -m tumnis.testing.run_worker --import tumnis.core.tests.integration._deliveries`,
so both processes register the same subscribers.

- `test.ping` (registered by tumnis.core.events as the example event): `testa.record` and
  `testb.record` insert `(event_id, subscriber)` into `test_deliveries`.
- `test.solo`: one subscriber, `testa.solo`, for kill tests that must not race a sibling.
- `test.flaky`: `testa.flaky_ok` always records; `testb.flaky_fail` raises
  `RuntimeError("boom")` while `FLAKY.failing` is set (max_attempts=3, base 0.05 s, cap 0.2 s).

"Once" is `count(*) = 1` per `(event_id, subscriber)` pair: the table has no unique key, so
a handler that runs twice shows up as two rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal
from uuid import UUID

import psycopg
from sqlalchemy import text

from tumnis.core import db
from tumnis.core.events import EventEnvelope, EventPayload, event_type, subscribe

OWNER_ROLE = "tumnis_owner"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS test_deliveries (
  event_id   uuid NOT NULL,
  subscriber text NOT NULL,
  at         timestamptz NOT NULL DEFAULT clock_timestamp()
);
GRANT SELECT, INSERT ON test_deliveries TO tumnis_app;
"""


@event_type("test.solo", 1)
class SoloV1(EventPayload):
    event_name: ClassVar[str] = "test.solo"
    schema_version: Literal[1] = 1
    note: str


@event_type("test.flaky", 1)
class FlakyV1(EventPayload):
    event_name: ClassVar[str] = "test.flaky"
    schema_version: Literal[1] = 1
    note: str


@dataclass
class Switch:
    failing: bool = True


FLAKY = Switch()
FLAKY_FAIL = "testb.flaky_fail"
FLAKY_OK = "testa.flaky_ok"


async def record(envelope: EventEnvelope, subscriber: str) -> None:
    """One row per handler call, in the event's workspace (the context run_handler sets)."""
    async with db.app_sessionmaker()() as session, session.begin():
        await session.execute(
            text("INSERT INTO test_deliveries (event_id, subscriber) VALUES (:e, :s)"),
            {"e": envelope.event_id, "s": subscriber},
        )


@subscribe("test.ping", name="testa.record")
async def testa_record(envelope: EventEnvelope) -> None:
    await record(envelope, "testa.record")


@subscribe("test.ping", name="testb.record")
async def testb_record(envelope: EventEnvelope) -> None:
    await record(envelope, "testb.record")


@subscribe("test.solo", name="testa.solo")
async def testa_solo(envelope: EventEnvelope) -> None:
    await record(envelope, "testa.solo")


@subscribe("test.flaky", name=FLAKY_OK)
async def testa_flaky_ok(envelope: EventEnvelope) -> None:
    await record(envelope, FLAKY_OK)


@subscribe("test.flaky", name=FLAKY_FAIL, max_attempts=3, base_delay_s=0.05, cap_s=0.2)
async def testb_flaky_fail(envelope: EventEnvelope) -> None:
    if FLAKY.failing:
        raise RuntimeError("boom")
    await record(envelope, FLAKY_FAIL)


def create_table(owner_dsn: str) -> None:
    """Create `test_deliveries` as the owner (libpq DSN), readable and writable by the app."""
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(CREATE_TABLE.encode())


def counts(owner_dsn: str) -> dict[tuple[UUID, str], int]:
    """(event_id, subscriber) -> number of handler calls recorded."""
    with psycopg.connect(owner_dsn) as conn:
        rows = conn.execute(
            "SELECT event_id, subscriber, count(*) FROM test_deliveries GROUP BY 1, 2"
        ).fetchall()
    return {(event_id, subscriber): n for event_id, subscriber, n in rows}
