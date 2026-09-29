"""`GET /v1/audit` and `GET /v1/audit.csv` (P0-15, SEC-3): the signed-in session's
workspace audit log, newest first (`auth="session"`).

- `GET /v1/audit`: keyset pages on `(occurred_at, id)` descending through P0-10's
  `paginate()`; filters `action`, `actor_type`, `from` (inclusive) and `to` (exclusive);
  `limit` 1 to 200, default 50; a malformed cursor is 400 `invalid_cursor`.
- `GET /v1/audit.csv`: the same filters, streamed. The export itself is audited as
  `audit.exported` (committed before the first byte, so a download cut short is still on
  record). A cell starting with `=`, `+`, `-`, `@`, tab or carriage return gets a leading
  `'`, so a spreadsheet shows it as text instead of running it as a formula.

Session seam: the routes are declared on `v1_router` with `RoutePolicy(auth="session")`
(P0-10). `require_session` answers with the workspace context of a session principal on
`request.state.principal` (set by P0-13's authentication middleware) or of
`request.state.session_context`, and 401 otherwise; tests override it.
"""

import csv
import io
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from tumnis.core import audit
from tumnis.core.pagination import Page, PageParams, SortKey, page_params, paginate
from tumnis.core.principal import principal_of
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext, tenant_session

router = v1_router("core", tags=["audit"])

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


AuditPage = Page[AuditEntry]


async def require_session(request: Request) -> WorkspaceContext:
    """The signed-in session's workspace context (see the module docstring)."""
    ctx = getattr(request.state, "session_context", None)
    if isinstance(ctx, WorkspaceContext):
        return ctx
    principal = principal_of(request)
    if principal.kind == "session" and not principal.anonymous:
        return principal.workspace_context()
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


def _where(ctx: WorkspaceContext, chosen: Filters) -> sa.Select[Any]:
    return sa.select(*_COLUMNS).where(_log.c.workspace_id == ctx.workspace_id, *chosen.where())


def _statement(ctx: WorkspaceContext, chosen: Filters) -> sa.Select[Any]:
    return _where(ctx, chosen).order_by(_log.c.occurred_at.desc(), _log.c.id.desc())


@router.get("/audit")
@route_policy(RoutePolicy(auth="session", paginated=True))
async def list_audit(
    ctx: Annotated[WorkspaceContext, Depends(require_session)],
    chosen: Annotated[Filters, Depends(filters)],
    page: Annotated[PageParams, Depends(page_params)],
) -> AuditPage:
    """The workspace's audit log, newest first."""
    async with tenant_session(ctx) as s:
        return await paginate(
            s,
            _where(ctx, chosen),
            keys=[SortKey(_log.c.occurred_at)],
            id_col=_log.c.id,
            cursor=page.cursor,
            limit=page.limit,
            model=AuditEntry,
            descending=True,
        )


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
@route_policy(RoutePolicy(auth="session"))
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
