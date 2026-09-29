"""Helpers for the usage counter spec tests (P0-21). No assertions live here: spec-guard
locks the test bodies, and this module is where later work packages plug in their shapes.

- `configured(db)`: tumnis.core.db pointed at the per-test database (no pooling).
- `create_task(ctx, occurred_at)`: one task creation, which emits one `task.created`.
  Until P0-18's `tasks.api` exists it writes that event's outbox row (and the NOTIFY)
  itself, in the workspace's transaction, as `emit` would; P0-18 swaps the body for a
  real `tasks.api` call and the tests keep calling it. Returns the event id.
- `outbox_envelope(db, event_id)`: the envelope the relay built from that outbox row.
- `counter_value(db, workspace_id, day, counter)`: `usage_counters.value`, read as the
  owner (None when there is no row).
- `ledger_rows(db, workspace_id, event_id)`: how many ledger rows hold that event.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    from datetime import date, datetime

    from tests._pg import DbUrls
    from tumnis.core.events import EventEnvelope
    from tumnis.core.tenancy import WorkspaceContext


@asynccontextmanager
async def configured(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


async def create_task(ctx: WorkspaceContext, occurred_at: datetime) -> uuid.UUID:
    """One task created in ctx's workspace; returns its `task.created` event id."""
    from sqlalchemy import insert  # noqa: PLC0415

    from tumnis.core.outbox import NOTIFY, outbox_table  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    event_id = uuid.uuid4()
    payload = {
        "task_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "label": None,
        "source": "user",
        "tainted": False,
        "schema_version": 1,
    }
    async with tenant_session(ctx) as session:
        await session.execute(
            insert(outbox_table).values(
                workspace_id=ctx.workspace_id,
                event_id=event_id,
                name="task.created",
                schema_version=1,
                occurred_at=occurred_at,
                payload=payload,
                trace_context={},
            )
        )
        await session.execute(NOTIFY)
    return event_id


def outbox_envelope(db: DbUrls, event_id: uuid.UUID) -> EventEnvelope:
    from tumnis.core.events import EventEnvelope  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        row = conn.execute("SELECT * FROM outbox WHERE event_id = %s", (event_id,)).fetchone()
    if row is None:
        raise LookupError(f"no outbox row for event {event_id}")
    return EventEnvelope.from_outbox_row(row)


def counter_value(db: DbUrls, workspace_id: uuid.UUID, day: date, counter: str) -> int | None:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(
            "SELECT value FROM usage_counters WHERE workspace_id = %s AND day = %s "
            "AND counter = %s",
            (workspace_id, day, counter),
        ).fetchone()
    return None if row is None else int(row[0])


def ledger_rows(db: DbUrls, workspace_id: uuid.UUID, event_id: uuid.UUID) -> int:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(
            "SELECT count(*) FROM usage_ledger WHERE workspace_id = %s AND event_id = %s",
            (workspace_id, event_id),
        ).fetchone()
    return 0 if row is None else int(row[0])
