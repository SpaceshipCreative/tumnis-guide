"""Sessions: create, resolve, slide, revoke (P0-13, SEC-1, R-21).

A session is a `sessions` row in the user's workspace holding HMAC(pepper, token), never
the token. The authentication middleware resolves the cookie through
`app.auth_resolve_session` (the workspace is unknown until then), refuses revoked and idle
ones (`expires_at = last_seen_at + 30 days`) and slides `last_seen_at` at most once an
hour, so a session in daily use never expires and an idle one ends after 30 days.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, cast
from uuid import UUID

from sqlalchemy import Table, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import db
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.principal import AuthFailure, Principal
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import ActorRef
from tumnis.modules.auth import rules
from tumnis.modules.auth.csrf import csrf_token, new_session_token, session_token_hmac
from tumnis.modules.auth.models import AuthSession

SESSIONS = cast("Table", AuthSession.__table__)
_RESOLVE: Final = text(
    "SELECT session_id, user_id, workspace_id, last_seen_at, expires_at, revoked_at"
    " FROM app.auth_resolve_session(:token_hmac)"
)


def user_context(workspace_id: UUID, user_id: UUID) -> WorkspaceContext:
    """Acting as the user in a workspace; also lets the transaction see the user's own
    `users` row (app.user_id)."""
    return WorkspaceContext(workspace_id, ActorRef(f"user:{user_id}"))


@dataclass(frozen=True)
class NewSession:
    id: UUID
    token: str  # the cookie value; shown once, never stored
    csrf: str


async def create(
    session: AsyncSession,
    *,
    user_id: UUID,
    now: datetime,
    user_agent: str | None,
    source_ip: str | None,
    second_factor: str,
) -> NewSession:
    """A new session row in the caller's transaction (the user's workspace context)."""
    token = new_session_token()
    session_id = uuid7()
    await session.execute(
        SESSIONS.insert().values(
            id=session_id,
            user_id=user_id,
            token_hmac=session_token_hmac(token),
            device_label=rules.device_label(user_agent),
            user_agent=user_agent,
            source_ip=source_ip,
            second_factor=second_factor,
            last_seen_at=now,
            expires_at=now + rules.IDLE_TIMEOUT,
        )
    )
    return NewSession(session_id, token, csrf_token(session_id))


async def resolve(token: str, now: datetime) -> Principal | AuthFailure:
    """The session principal for a cookie value, or why it is refused."""
    async with db.app_sessionmaker()() as s, s.begin():
        row = (await s.execute(_RESOLVE, {"token_hmac": session_token_hmac(token)})).first()
    if row is None or row.revoked_at is not None:
        return AuthFailure("unauthenticated")
    if rules.session_expired(row.last_seen_at, now) or now >= row.expires_at:
        return AuthFailure("session_expired")
    if rules.should_slide(row.last_seen_at, now):
        async with tenant_session(user_context(row.workspace_id, row.user_id)) as s:
            await s.execute(
                update(SESSIONS)
                .where(SESSIONS.c.id == row.session_id, SESSIONS.c.revoked_at.is_(None))
                .values(last_seen_at=now, expires_at=now + rules.IDLE_TIMEOUT)
            )
    return Principal(
        kind="session",
        workspace_id=row.workspace_id,
        subject_id=row.user_id,
        session_id=row.session_id,
        csrf_token=csrf_token(row.session_id),
    )


def _active(user_id: UUID, now: datetime) -> list[Any]:
    return [
        SESSIONS.c.user_id == user_id,
        SESSIONS.c.revoked_at.is_(None),
        SESSIONS.c.deleted_at.is_(None),
        SESSIONS.c.expires_at > now,
    ]


async def revoke(session: AsyncSession, user_id: UUID, session_id: UUID, now: datetime) -> bool:
    result = await session.execute(
        update(SESSIONS)
        .where(SESSIONS.c.id == session_id, *_active(user_id, now))
        .values(revoked_at=now)
        .returning(SESSIONS.c.id)
    )
    return result.first() is not None


async def revoke_others(
    session: AsyncSession, user_id: UUID, keep: UUID, now: datetime
) -> list[UUID]:
    result = await session.execute(
        update(SESSIONS)
        .where(SESSIONS.c.id != keep, *_active(user_id, now))
        .values(revoked_at=now)
        .returning(SESSIONS.c.id)
    )
    return [row.id for row in result]


# --- Listing -----------------------------------------------------------------------------


def encode_cursor(row_id: UUID) -> str:
    raw = json.dumps({"v": 1, "k": [], "id": str(row_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> UUID:
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if value["v"] != 1:
            raise ValueError(cursor)
        return UUID(value["id"])
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise ProblemError(400, "invalid_cursor", "The cursor does not decode") from exc


async def page(
    session: AsyncSession, user_id: UUID, now: datetime, *, cursor: str | None, limit: int
) -> tuple[list[Any], str | None]:
    """The user's active sessions, newest first (keyset on id; uuidv7 is creation order)."""
    statement = (
        select(
            SESSIONS.c.id,
            SESSIONS.c.device_label,
            SESSIONS.c.user_agent,
            func.host(SESSIONS.c.source_ip).label("source_ip"),
            SESSIONS.c.created_at,
            SESSIONS.c.last_seen_at,
            SESSIONS.c.expires_at,
        )
        .where(*_active(user_id, now))
        .order_by(SESSIONS.c.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        statement = statement.where(SESSIONS.c.id < decode_cursor(cursor))
    rows = list((await session.execute(statement)).all())
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, encode_cursor(rows[-1].id) if more and rows else None
