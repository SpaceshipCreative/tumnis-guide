"""emit(): one outbox row and a NOTIFY in the caller's transaction (P0-07, ADR-0011).

`NOTIFY` inside a transaction is delivered only on commit, so a rolled-back write wakes
nobody; the relay (tumnis.core.events) wakes on it, or finds the row on its next poll.
"""

from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import TIMESTAMP, Column, Integer, Table, Text, Uuid, insert, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import ids, tenancy
from tumnis.core.base import Base
from tumnis.core.events import EventPayload, EventSchemaError, registry

__all__ = ["EventSchemaError", "emit", "outbox_table"]

# Mirrors revision core_0004_outbox (the migration creates it; this is for queries).
outbox_table = Table(
    "outbox",
    Base.metadata,
    Column("id", Uuid, primary_key=True, server_default=text("uuidv7()")),
    Column(
        "workspace_id",
        Uuid,
        nullable=False,
        server_default=text("app.current_workspace_id()"),
    ),
    Column("event_id", Uuid, nullable=False, server_default=text("uuidv7()")),
    Column("name", Text, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("actor", Text, nullable=False, server_default=text("app.current_actor()")),
    Column("occurred_at", TIMESTAMP(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("trace_context", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("sent_at", TIMESTAMP(timezone=True)),
)

NOTIFY = text("SELECT pg_notify('outbox', '')")


async def emit(session: AsyncSession, payload: EventPayload, *, occurred_at: datetime) -> UUID:
    """Insert one outbox row and NOTIFY in the caller's transaction. Both commit or roll back
    together. Must be called inside tenant_session (the row's workspace comes from the
    context). The payload is validated against its registered model before any SQL."""
    ctx = tenancy.current()
    if ctx is None:
        raise RuntimeError("emit() outside a workspace context")
    model = registry.model(payload.event_name, payload.schema_version)
    try:
        body = model.model_validate(payload.model_dump(warnings=False)).model_dump(mode="json")
    except ValidationError as exc:
        raise EventSchemaError(
            f"{payload.event_name} v{payload.schema_version}: {exc.error_count()} invalid "
            f"field(s): {exc}"
        ) from exc
    event_id = ids.uuid7()
    await session.execute(
        insert(outbox_table).values(
            workspace_id=ctx.workspace_id,
            event_id=event_id,
            name=payload.event_name,
            schema_version=payload.schema_version,
            occurred_at=occurred_at,
            payload=body,
            trace_context={},  # P0-27: telemetry.current_trace_context()
        )
    )
    await session.execute(NOTIFY)
    return event_id
