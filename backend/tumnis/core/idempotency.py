"""Idempotent writes: a retry with the same Idempotency-Key replays the stored response for
24 hours (P0-10, REL-2).

`TumnisRoute` sends every write whose policy says `idempotent=True` through `run`. The key
row, the business change and the stored response share one transaction: the endpoint's
`SessionDep` is the session opened here, so they commit together, and a crash or a 5xx
stores nothing and changes nothing. A second request with the key waits on the unique
index `(workspace_id, principal, key)` until the first commits, then replays its response
with `Idempotent-Replayed: true`; the same key with another method, route or body is 422
`idempotency_mismatch`. Keys are scoped by workspace and principal. A raised error rolls
back the whole write and stores nothing, so a retry re-derives the same answer.

An anonymous caller passes straight through (the route's auth dependency answers 401),
so authentication errors come before `idempotency_key_required`.

Streaming responses are not allowed on idempotent routes (the route registry refuses
them). The purge of expired keys is P0-19's housekeeping; an expired key found here is
replaced.
"""

import hashlib
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Annotated, Any, Final

from fastapi import Depends, Request
from sqlalchemy import (
    TIMESTAMP,
    Column,
    Integer,
    LargeBinary,
    Table,
    Text,
    Uuid,
    delete,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from tumnis.core.base import Base
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.principal import principal_of
from tumnis.core.tenancy import tenant_session

IDEMPOTENCY_TTL: Final = timedelta(hours=24)  # REL-2
KEY_RE: Final = re.compile(r"^[A-Za-z0-9_\-:.]{8,255}$")
KEY_HEADER: Final = "Idempotency-Key"
REPLAYED_HEADER: Final = "Idempotent-Replayed"
# Only these response headers are stored and replayed; never Set-Cookie.
STORED_HEADERS: Final = ("content-type", "location", "etag")

# Mirrors revision core_0007_idempotency (the migration creates it; this is for queries).
idempotency_keys = Table(
    "idempotency_keys",
    Base.metadata,
    Column("id", Uuid, primary_key=True, server_default=text("uuidv7()")),
    Column("workspace_id", Uuid, nullable=False, server_default=text("app.current_workspace_id()")),
    Column("principal", Text, nullable=False),
    Column("key", Text, nullable=False),
    Column("route", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("request_hash", LargeBinary, nullable=False),
    Column("response_status", Integer),
    Column("response_headers", JSONB),
    Column("response_body", LargeBinary),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("expires_at", TIMESTAMP(timezone=True), nullable=False),
)
_t = idempotency_keys

Handler = Callable[[Request], Awaitable[Response]]


class _Rollback(Exception):  # noqa: N818  # control flow: a 5xx leaves the transaction
    def __init__(self, response: Response) -> None:
        super().__init__("rollback")
        self.response = response


def canonical(body: bytes) -> bytes:
    """JSON with sorted keys and no spaces, so a retry that reorders keys is the same
    request; anything else as it came."""
    try:
        value = json.loads(body)
    except ValueError:
        return body
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def request_hash(method: str, route: str, body: bytes) -> bytes:
    return hashlib.sha256(f"{method}\n{route}\n".encode() + canonical(body)).digest()


def clock_of(request: Request) -> Clock:
    clock = getattr(request.app.state, "clock", None)
    return clock if clock is not None else SystemClock()


def _redacted_body(body: bytes, fields: tuple[str, ...]) -> bytes:
    """The body as stored: fields named in `redact_on_replay` removed, and `redacted`
    listing them so a replay says the secret was shown once already."""
    if not fields:
        return body
    try:
        value = json.loads(body)
    except ValueError:
        return body
    if not isinstance(value, dict):
        return body
    removed = sorted(f for f in fields if f in value)
    for name in removed:
        del value[name]
    value["redacted"] = removed
    return json.dumps(value, separators=(",", ":")).encode()


def replay(row: Mapping[Any, Any]) -> Response:
    headers = dict(row["response_headers"] or {})
    headers[REPLAYED_HEADER] = "true"
    return Response(
        content=bytes(row["response_body"] or b""),
        status_code=row["response_status"],
        headers=headers,
    )


async def run(request: Request, call_next: Handler) -> Response:
    """Run `call_next` once per (workspace, principal, key); replay otherwise."""
    principal = principal_of(request)
    if principal.anonymous:
        return await call_next(request)
    key = request.headers.get(KEY_HEADER)
    if key is None or not KEY_RE.match(key):
        raise ProblemError(
            400,
            "idempotency_key_required",
            "Send an Idempotency-Key header (8 to 255 of A-Z a-z 0-9 _ - : .) on every write",
        )
    route = str(getattr(request.state, "route_template", request.url.path))
    body = await request.body()
    digest = request_hash(request.method, route, body)
    now = clock_of(request).now()
    fields = {
        "principal": principal.key,
        "key": key,
        "route": route,
        "method": request.method,
        "request_hash": digest,
        "expires_at": now + IDEMPOTENCY_TTL,
    }
    new_row = (
        insert(_t)
        .values(**fields)
        .on_conflict_do_nothing(index_elements=[_t.c.workspace_id, _t.c.principal, _t.c.key])
        .returning(_t.c.id)
    )
    try:
        async with tenant_session(principal.workspace_context()) as s:
            request.state.session = s
            inserted = (await s.execute(new_row)).first()
            if inserted is None:  # blocked on the unique index until the first one committed
                found = (
                    (
                        await s.execute(
                            select(_t)
                            .where(_t.c.principal == principal.key, _t.c.key == key)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                if found["expires_at"] <= now:
                    await s.execute(delete(_t).where(_t.c.id == found["id"]))
                    inserted = (await s.execute(new_row)).first()
                    if inserted is None:  # pragma: no cover  # we hold the row lock
                        raise ProblemError(409, "idempotency_in_progress")
                elif (found["request_hash"], found["route"], found["method"]) != (
                    digest,
                    route,
                    request.method,
                ):
                    raise ProblemError(
                        422,
                        "idempotency_mismatch",
                        "This key was used with a different request",
                    )
                elif found["response_status"] is None:  # pragma: no cover  # never committed
                    raise ProblemError(409, "idempotency_in_progress")
                else:
                    return replay(found)
            response = await call_next(request)
            if response.status_code >= 500:  # noqa: PLR2004
                raise _Rollback(response)
            stored = getattr(response, "body", None)
            if not isinstance(stored, bytes | memoryview):
                raise TypeError("idempotent routes cannot stream their response")
            policy = getattr(request.state, "policy", None)
            redact = tuple(getattr(policy, "redact_on_replay", ()))
            headers = {
                name: response.headers[name] for name in STORED_HEADERS if name in response.headers
            }
            await s.execute(
                update(_t)
                .where(_t.c.id == inserted.id)
                .values(
                    response_status=response.status_code,
                    response_headers=headers,
                    response_body=_redacted_body(bytes(stored), redact),
                )
            )
            return response
    except _Rollback as rollback:
        return rollback.response
    finally:
        request.state.session = None


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """The write's session inside an idempotent request (commits with the stored response);
    otherwise a tenant session of the request's principal, committed when the endpoint
    returns. Endpoints never commit themselves."""
    session = getattr(request.state, "session", None)
    if isinstance(session, AsyncSession):
        yield session
        return
    async with tenant_session(principal_of(request).workspace_context()) as fresh:
        yield fresh


# scope="function": the transaction ends before the response is sent, so a client never
# reads a response whose write has not committed.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
