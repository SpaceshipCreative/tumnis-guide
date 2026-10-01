"""Helpers for the usage counter spec tests (P0-21). No assertions live here: spec-guard
locks the test bodies, and this module is where later work packages plug in their shapes.

- `configured(db)`: tumnis.core.db pointed at the per-test database (no pooling).
- `create_task(ctx, occurred_at)`: one task created through `tasks.api.create_task`
  (P0-18), which emits one `task.created`; returns that event's id. Its project is
  inserted directly, so the outbox holds no `project.created` beside it (the tests relay
  exactly one row). The one import of another module in usage (tests only; the
  import-linter contract ignores it).
- `outbox_envelope(db, event_id)`: the envelope the relay built from that outbox row.
- `counter_value(db, workspace_id, day, counter)`: `usage_counters.value`, read as the
  owner (None when there is no row).
- `ledger_rows(db, workspace_id, event_id)`: how many ledger rows hold that event.
- `outbound_blocked()` (P1-18): sockets refused for everything but loopback.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
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
    from sqlalchemy import select, text  # noqa: PLC0415

    from tumnis.core.outbox import outbox_table  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    async with tenant_session(ctx) as session:
        project_id = await session.scalar(
            text("INSERT INTO projects (name, sort_key) VALUES (:name, 'a0') RETURNING id"),
            {"name": f"Usage project {uuid.uuid4().hex[:8]}"},
        )
        data = tasks.TaskCreate(project_id=project_id, title="Counted task")
        task = await tasks.create_task(session, ctx.actor, data, now=occurred_at)
        event_id: uuid.UUID | None = await session.scalar(
            select(outbox_table.c.event_id).where(
                outbox_table.c.name == "task.created",
                outbox_table.c.payload["task_id"].astext == str(task.id),
            )
        )
    if event_id is None:
        raise LookupError(f"no task.created outbox row for task {task.id}")
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


@contextmanager
def outbound_blocked() -> Iterator[None]:
    """P1-18: no Python socket may connect anywhere but loopback while inside. The database
    is reached through libpq, which pytest-socket does not guard, so Postgres still works
    and anything else (a metrics service, a CDN) fails the request."""
    import pytest_socket  # noqa: PLC0415

    pytest_socket.socket_allow_hosts(["127.0.0.1", "::1"], allow_unix_socket=True)
    try:
        yield
    finally:
        pytest_socket._remove_restrictions()  # its public enable leaves connect guarded


def ledger_rows(db: DbUrls, workspace_id: uuid.UUID, event_id: uuid.UUID) -> int:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(
            "SELECT count(*) FROM usage_ledger WHERE workspace_id = %s AND event_id = %s",
            (workspace_id, event_id),
        ).fetchone()
    return 0 if row is None else int(row[0])
