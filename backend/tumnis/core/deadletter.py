"""Dead letters: deliveries that ran out of attempts, with list, retry and discard
(P0-07, REL-3).

`deliver_event` (tumnis.core.events) records a row per (workspace, event, subscriber) when
its last attempt fails, and resolves it when a later delivery of the same pair succeeds.
The api process lists them and, on retry, enqueues `deliver_event` again through a
`DBOSClient` (it never launches DBOS itself) with workflow ID
`<event_id>:<subscriber>:retry:<n>`. Every write is versioned: a stale version or a row
that is no longer `open` is a 409.

The router (`/v1/dead-letters`) is session-only through the shared session seam
(`audit_router.require_session`, filled by P0-13); retry and discard are audited
(SEC-3). P0-10's `versioning.StaleVersion` and `pagination.Page` replace the
local ones here when they land.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Column,
    Integer,
    RowMapping,
    Table,
    Text,
    Uuid,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import audit, tenancy
from tumnis.core.audit_router import require_session
from tumnis.core.base import Base
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.tenancy import WorkspaceContext

if TYPE_CHECKING:
    from dbos import DBOSClient

EVENTS_QUEUE: Final = "events"  # tumnis.core.events.EVENTS_QUEUE (not imported: no cycle)
DELIVER_WORKFLOW: Final = "deliver_event"
LIMIT_DEFAULT, LIMIT_MAX = 50, 200  # plan defaults (P0-10)
STATUSES: Final = ("open", "retrying", "resolved", "discarded")
RETRIED: Final = "dead_letter.retried"  # SEC-3 audit actions
DISCARDED: Final = "dead_letter.discarded"

# Mirrors revision core_0004_outbox (the migration creates it; this is for queries).
dead_letters_table = Table(
    "dead_letters",
    Base.metadata,
    Column("id", Uuid, primary_key=True, server_default=text("uuidv7()")),
    Column("workspace_id", Uuid, nullable=False, server_default=text("app.current_workspace_id()")),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("deleted_at", TIMESTAMP(timezone=True)),
    Column("created_by", Text, nullable=False, server_default=text("app.current_actor()")),
    Column("event_id", Uuid, nullable=False),
    Column("subscriber", Text, nullable=False),
    Column("event_name", Text, nullable=False),
    Column("envelope", JSONB, nullable=False),
    Column("error", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("retries", Integer, nullable=False, server_default=text("0")),
    Column("last_at", TIMESTAMP(timezone=True), nullable=False),
    Column("status", Text, nullable=False, server_default=text("'open'")),
    CheckConstraint("status IN ('open', 'retrying', 'resolved', 'discarded')"),
)
_t = dead_letters_table


class DeadLetterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_id: uuid.UUID
    event_name: str
    subscriber: str
    error: str
    attempts: int
    retries: int
    last_at: datetime
    status: str
    version: int


class Page(BaseModel):
    items: list[DeadLetterOut]
    next_cursor: str | None = None


class DeadLetterError(Exception):
    status: int = 409
    code: str = "conflict"

    def __init__(self, detail: str, current: Mapping[Any, Any] | None = None) -> None:
        super().__init__(detail)
        self.current = dict(current) if current is not None else None


class DeadLetterNotOpen(DeadLetterError):  # noqa: N818  # plan name
    code = "dead_letter_not_open"


class StaleVersion(DeadLetterError):  # noqa: N818  # plan name (P0-10's versioning)
    code = "stale_version"


class DeadLetterNotFound(DeadLetterError):  # noqa: N818  # the plan's error family
    status = 404
    code = "not_found"


# --- SQL, all of it, in the workspace in context -----------------------------------------


class DeadLetterRepo:
    """Every dead-letter statement. Runs on a tenant_session, so row-level security keeps
    it inside the workspace in context."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        event_id: uuid.UUID,
        subscriber: str,
        event_name: str,
        envelope: Mapping[str, Any],
        error: str,
        attempts: int,
    ) -> None:
        """Open (or reopen, after a failed retry) the dead letter for this pair."""
        stmt = insert(_t).values(
            event_id=event_id,
            subscriber=subscriber,
            event_name=event_name,
            envelope=dict(envelope),
            error=error,
            attempts=attempts,
            last_at=func.now(),
            status="open",
        )
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[_t.c.workspace_id, _t.c.event_id, _t.c.subscriber],
                set_={
                    "error": stmt.excluded.error,
                    "attempts": stmt.excluded.attempts,
                    "last_at": stmt.excluded.last_at,
                    "envelope": stmt.excluded.envelope,
                    "status": "open",
                },
            )
        )

    async def resolve(self, event_id: uuid.UUID, subscriber: str) -> None:
        """A later delivery of the pair succeeded: close its dead letter, if it has one."""
        await self.session.execute(
            update(_t)
            .where(
                _t.c.event_id == event_id,
                _t.c.subscriber == subscriber,
                _t.c.status.in_(("open", "retrying")),
            )
            .values(status="resolved", last_at=func.now())
        )

    async def page(self, status: str, cursor: uuid.UUID | None, limit: int) -> list[RowMapping]:
        stmt = select(_t).where(_t.c.status == status, _t.c.deleted_at.is_(None))
        if cursor is not None:
            stmt = stmt.where(_t.c.id > cursor)
        rows = await self.session.execute(stmt.order_by(_t.c.id).limit(limit))
        return list(rows.mappings().all())

    async def transition(
        self, row_id: uuid.UUID, expected_version: int, *, to: str, retry: bool = False
    ) -> RowMapping:
        """open -> `to` at `expected_version`, bumping `retries` for a retry. Two callers
        with the same version: the second waits on the first's row lock, then matches
        nothing and gets a 409."""
        values: dict[str, Any] = {"status": to}
        if retry:
            values["retries"] = _t.c.retries + 1
        row = (
            (
                await self.session.execute(
                    update(_t)
                    .where(
                        _t.c.id == row_id,
                        _t.c.version == expected_version,
                        _t.c.status == "open",
                        _t.c.deleted_at.is_(None),
                    )
                    .values(**values)
                    .returning(*_t.c)
                )
            )
            .mappings()
            .first()
        )
        if row is not None:
            return row
        current = (
            (await self.session.execute(select(_t).where(_t.c.id == row_id))).mappings().first()
        )
        if current is None or current["deleted_at"] is not None:
            raise DeadLetterNotFound(f"no dead letter {row_id}")
        if current["status"] != "open":
            raise DeadLetterNotOpen(f"dead letter {row_id} is {current['status']}", current)
        raise StaleVersion(
            f"dead letter {row_id} is at version {current['version']}, not {expected_version}",
            current,
        )


# --- The DBOS client the api process enqueues with ---------------------------------------

_client: "DBOSClient | None" = None
_client_owned = False  # built here (and so destroyed here), not handed in by use_client
_client_url: str | None = None


def configure(system_database_url: str | None) -> None:
    """Where the client connects; it is built on first use (create_app does no I/O). Drops
    any client in use, so a new app never enqueues through an old one."""
    global _client_url  # noqa: PLW0603  # process-wide client settings
    close()
    _client_url = system_database_url


def use_client(client: "DBOSClient | None") -> None:
    """Use this client (tests: the `dbos_client` fixture); its owner destroys it."""
    global _client, _client_owned  # process-wide client
    _client, _client_owned = client, False


def _dbos_client() -> "DBOSClient":
    global _client, _client_owned  # built once per process
    if _client is None:
        if _client_url is None:
            raise RuntimeError("tumnis.core.deadletter has no DBOS system database URL")
        from dbos import DBOSClient  # noqa: PLC0415

        _client, _client_owned = DBOSClient(system_database_url=_client_url, lazy=True), True
    return _client


def close() -> None:
    """Destroy a client this module built (application shutdown)."""
    global _client, _client_owned  # process-wide client
    if _client is not None and _client_owned:
        _client.destroy()
    _client, _client_owned = None, False


# --- Public functions ----------------------------------------------------------------------


async def list_dead_letters(
    ctx: WorkspaceContext, *, status: str = "open", cursor: str | None = None, limit: int = 50
) -> Page:
    """The workspace's dead letters in `status`, oldest first; `next_cursor` continues."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    limit = max(1, min(limit, LIMIT_MAX))
    after = uuid.UUID(cursor) if cursor else None
    async with tenancy.tenant_session(ctx) as session:
        rows = await DeadLetterRepo(session).page(status, after, limit)
    items = [DeadLetterOut.model_validate(row) for row in rows]
    next_cursor = str(items[-1].id) if len(items) == limit else None
    return Page(items=items, next_cursor=next_cursor)


async def retry(
    ctx: WorkspaceContext,
    dead_letter_id: uuid.UUID,
    *,
    expected_version: int,
    clock: Clock | None = None,
) -> DeadLetterOut:
    """open -> retrying; enqueues deliver_event through DBOSClient with workflow ID
    f"{event_id}:{subscriber}:retry:{retries+1}". Anything but 'open' -> 409
    dead_letter_not_open; another version -> 409 stale_version. Audited as
    `dead_letter.retried` in the same transaction (SEC-3)."""
    async with tenancy.tenant_session(ctx) as session:
        row = await DeadLetterRepo(session).transition(
            dead_letter_id, expected_version, to="retrying", retry=True
        )
        await _audit(session, RETRIED, row, clock)
        wf_id = f"{row['event_id']}:{row['subscriber']}:retry:{row['retries']}"
        # Enqueued before commit: if the enqueue fails the row stays open. The workflow ID
        # makes a repeated enqueue return the same workflow.
        await _dbos_client().enqueue_async(
            {
                "queue_name": EVENTS_QUEUE,
                "workflow_name": DELIVER_WORKFLOW,
                "workflow_id": wf_id,
                "deduplication_id": wf_id,
            },
            row["subscriber"],
            row["envelope"],
        )
    return DeadLetterOut.model_validate(row)


async def discard(
    ctx: WorkspaceContext,
    dead_letter_id: uuid.UUID,
    *,
    expected_version: int,
    clock: Clock | None = None,
) -> DeadLetterOut:
    """open -> discarded: the item is closed and can no longer be retried. Audited as
    `dead_letter.discarded` in the same transaction (SEC-3)."""
    async with tenancy.tenant_session(ctx) as session:
        row = await DeadLetterRepo(session).transition(
            dead_letter_id, expected_version, to="discarded"
        )
        await _audit(session, DISCARDED, row, clock)
    return DeadLetterOut.model_validate(row)


async def _audit(session: AsyncSession, action: str, row: RowMapping, clock: Clock | None) -> None:
    await audit.record(
        session,
        action,
        target=("dead_letter", row["id"]),
        details={
            "event_id": str(row["event_id"]),
            "event_name": row["event_name"],
            "subscriber": row["subscriber"],
            "retries": row["retries"],
        },
        occurred_at=(clock or SystemClock()).now(),
    )


# --- Router: /v1/dead-letters, session only -------------------------------------------------


# The one session seam (P0-15's audit_router.require_session): P0-13 fills it, and the
# tests' signed-in client overrides it for every session-only route at once.
Ctx = Annotated[WorkspaceContext, Depends(require_session)]


class VersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int


router = APIRouter(prefix="/v1/dead-letters", tags=["dead_letters"])


def _http(exc: DeadLetterError) -> HTTPException:
    return HTTPException(
        status_code=exc.status, detail={"code": exc.code, "detail": str(exc), "current": None}
    )


@router.get("", response_model=Page)
async def get_dead_letters(
    ctx: Ctx,
    status: Annotated[str, Query(pattern="^(open|retrying|resolved|discarded)$")] = "open",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = LIMIT_DEFAULT,
) -> Page:
    return await list_dead_letters(ctx, status=status, cursor=cursor, limit=limit)


@router.post("/{dead_letter_id}/retry", response_model=DeadLetterOut)
async def post_retry(
    dead_letter_id: uuid.UUID, body: VersionIn, ctx: Ctx, request: Request
) -> DeadLetterOut:
    try:
        return await retry(
            ctx, dead_letter_id, expected_version=body.version, clock=request.app.state.clock
        )
    except DeadLetterError as exc:
        raise _http(exc) from exc


@router.post("/{dead_letter_id}/discard", response_model=DeadLetterOut)
async def post_discard(
    dead_letter_id: uuid.UUID, body: VersionIn, ctx: Ctx, request: Request
) -> DeadLetterOut:
    try:
        return await discard(
            ctx, dead_letter_id, expected_version=body.version, clock=request.app.state.clock
        )
    except DeadLetterError as exc:
        raise _http(exc) from exc
