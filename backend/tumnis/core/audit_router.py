"""`GET /v1/audit` and `GET /v1/audit.csv` (P0-15, SEC-3): the signed-in session's
workspace audit log, newest first (`auth="session"`).

- `GET /v1/audit`: keyset pages on `(occurred_at, id)` descending; filters `action`,
  `actor_type`, `from` (inclusive) and `to` (exclusive); `limit` 1 to 200, default 50; the
  cursor is base64url(JSON {"v": 1, "k": [occurred_at], "id": id}), the shape P0-10's
  `paginate()` uses, and a malformed one is 400 `invalid_cursor`.
- `GET /v1/audit.csv`: the same filters, streamed. The export itself is audited as
  `audit.exported` (committed before the first byte, so a download cut short is still on
  record). A cell starting with `=`, `+`, `-`, `@`, tab or carriage return gets a leading
  `'`, so a spreadsheet shows it as text instead of running it as a formula.

Session seam: P0-10 (`TumnisRoute`, `RoutePolicy(auth="session")`) and P0-13 (the
authentication middleware) are not built yet. Until then `require_session` reads the
signed-in context from `request.state.session_context`, which nothing sets, so every call
is 401; P0-13 sets it (or replaces this dependency) and P0-10 moves these routes onto
`v1_router` with their policy.
"""

import base64
import binascii
import csv
import io
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from tumnis.core import audit
from tumnis.core.tenancy import WorkspaceContext, tenant_session

router = APIRouter(prefix="/v1", tags=["audit"])

LIMIT_DEFAULT: Final = 50  # plan default, as P0-10's pagination
LIMIT_MAX: Final = 200
EXPORTED: Final = "audit.exported"
FORMULA_START: Final = ("=", "+", "-", "@", "\t", "\r")
CSV_FLUSH_BYTES: Final = 64 * 1024

ActorType = Literal["user", "api_key", "task_token", "device", "system"]

_log = sa.table(
    "audit_log",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("seq", sa.BigInteger()),
    sa.column("occurred_at", sa.TIMESTAMP(timezone=True)),
    sa.column("actor_type", sa.Text()),
    sa.column("actor_id", sa.Uuid()),
    sa.column("action", sa.Text()),
    sa.column("target_type", sa.Text()),
    sa.column("target_id", sa.Uuid()),
    sa.column("source_ip", sa.Text()),
    sa.column("user_agent", sa.Text()),
    sa.column("correlation_id", sa.Text()),
    sa.column("reason", sa.Text()),
    sa.column("details", sa.JSON()),
)
_COLUMNS: Final = (
    _log.c.id,
    _log.c.seq,
    _log.c.occurred_at,
    _log.c.actor_type,
    _log.c.actor_id,
    _log.c.action,
    _log.c.target_type,
    _log.c.target_id,
    sa.func.host(_log.c.source_ip).label("source_ip"),
    _log.c.user_agent,
    _log.c.correlation_id,
    _log.c.reason,
    _log.c.details,
)
CSV_HEADER: Final = tuple(c.name for c in _COLUMNS if c.name != "id")


class AuditEntry(BaseModel):
    id: UUID
    seq: int
    occurred_at: datetime
    actor_type: str
    actor_id: UUID | None
    action: str
    target_type: str | None
    target_id: UUID | None
    source_ip: str | None
    user_agent: str | None
    correlation_id: str | None
    reason: str | None
    details: dict[str, Any]


class AuditPage(BaseModel):
    items: list[AuditEntry]
    next_cursor: str | None


async def require_session(request: Request) -> WorkspaceContext:
    """The signed-in session's workspace context (see the module docstring)."""
    ctx = getattr(request.state, "session_context", None)
    if isinstance(ctx, WorkspaceContext):
        return ctx
    raise HTTPException(status_code=401, detail="unauthenticated")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class Filters:
    action: str | None
    actor_type: ActorType | None
    since: datetime | None
    until: datetime | None

    def where(self) -> list[sa.ColumnElement[bool]]:
        clauses: list[sa.ColumnElement[bool]] = []
        if self.action is not None:
            clauses.append(_log.c.action == self.action)
        if self.actor_type is not None:
            clauses.append(_log.c.actor_type == self.actor_type)
        if self.since is not None:
            clauses.append(_log.c.occurred_at >= self.since)
        if self.until is not None:
            clauses.append(_log.c.occurred_at < self.until)
        return clauses

    def as_details(self) -> dict[str, str]:
        values = {
            "action": self.action,
            "actor_type": self.actor_type,
            "from": self.since.isoformat() if self.since else None,
            "to": self.until.isoformat() if self.until else None,
        }
        return {k: v for k, v in values.items() if v is not None}


def filters(
    action: Annotated[str | None, Query(max_length=200)] = None,
    actor_type: ActorType | None = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
) -> Filters:
    return Filters(action, actor_type, _utc(since), _utc(until))


def _statement(ctx: WorkspaceContext, chosen: Filters) -> sa.Select[Any]:
    return (
        sa.select(*_COLUMNS)
        .where(_log.c.workspace_id == ctx.workspace_id, *chosen.where())
        .order_by(_log.c.occurred_at.desc(), _log.c.id.desc())
    )


def encode_cursor(occurred_at: datetime, row_id: UUID) -> str:
    raw = json.dumps({"v": 1, "k": [occurred_at.isoformat()], "id": str(row_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        if value["v"] != 1:
            raise ValueError(cursor)
        at = datetime.fromisoformat(value["k"][0])
        return at if at.tzinfo else at.replace(tzinfo=UTC), UUID(value["id"])
    except (binascii.Error, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_cursor") from exc


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    ctx: Annotated[WorkspaceContext, Depends(require_session)],
    chosen: Annotated[Filters, Depends(filters)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = LIMIT_DEFAULT,
) -> AuditPage:
    """The workspace's audit log, newest first."""
    statement = _statement(ctx, chosen)
    if cursor is not None:
        at, row_id = decode_cursor(cursor)
        statement = statement.where(sa.tuple_(_log.c.occurred_at, _log.c.id) < (at, row_id))
    async with tenant_session(ctx) as s:
        rows = (await s.execute(statement.limit(limit + 1))).mappings().all()
    items = [AuditEntry.model_validate(dict(row)) for row in rows[:limit]]
    more = len(rows) > limit
    last = items[-1] if items else None
    next_cursor = encode_cursor(last.occurred_at, last.id) if more and last else None
    return AuditPage(items=items, next_cursor=next_cursor)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        text = value.astimezone(UTC).isoformat()
    elif isinstance(value, dict | list):
        text = json.dumps(value, sort_keys=True)
    else:
        text = str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


async def _csv(ctx: WorkspaceContext, chosen: Filters) -> AsyncIterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    async with tenant_session(ctx) as s:
        result = await s.stream(_statement(ctx, chosen))
        async for row in result.mappings():
            writer.writerow([_cell(row[name]) for name in CSV_HEADER])
            if buffer.tell() >= CSV_FLUSH_BYTES:
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate()
    yield buffer.getvalue()


@router.get("/audit.csv", response_class=StreamingResponse)
async def export_audit_csv(
    request: Request,
    ctx: Annotated[WorkspaceContext, Depends(require_session)],
    chosen: Annotated[Filters, Depends(filters)],
) -> StreamingResponse:
    """The workspace's audit log as CSV, newest first; the export is audited."""
    async with tenant_session(ctx) as s:
        await audit.record(
            s,
            EXPORTED,
            details={"filters": chosen.as_details()},
            occurred_at=request.app.state.clock.now(),
        )
    return StreamingResponse(
        _csv(ctx, chosen),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )
