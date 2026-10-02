"""Workspace context applied on every transaction (P0-06, ADR-0009).

A `WorkspaceContext` lives in a context variable. SQLAlchemy's `after_begin` hook on the
sync `Session` class (which also fires for `AsyncSession`, a wrapper around one) runs
`set_config('app.workspace_id', ..., true)` and `set_config('app.actor', ..., true)` at the
start of every transaction, including a second `begin()` in the same session. The settings
are transaction-local, so they cannot leak through PgBouncer's transaction pooling. A
`user:<uuid>` actor also sets `app.user_id`, which the global `users` table's policy
matches (P0-13): a signed-in user reads its own row and no other. No
context means an empty workspace setting, which row-level security matches with no row:
fail closed.

Workflow steps use `tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR))`; request
handlers get their context from the auth dependency (P0-13).
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from tumnis.core import db
from tumnis.core.types import ActorRef


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: UUID
    actor: ActorRef  # "system" or "<kind>:<uuid>" (tumnis.core.types)


_ctx: ContextVar[WorkspaceContext | None] = ContextVar("tumnis_workspace", default=None)

_APPLY = text(
    "SELECT set_config('app.workspace_id', :ws, true), set_config('app.actor', :actor, true),"
    " set_config('app.user_id', :user_id, true)"
)
_USER_PREFIX = "user:"


def _user_id(ctx: WorkspaceContext | None) -> str:
    """The user the context acts as (`user:<uuid>` actors), for the `users` table's
    `self_only` policy (P0-13); empty for every other actor, which matches no row."""
    actor = str(ctx.actor) if ctx else ""
    return actor.removeprefix(_USER_PREFIX) if actor.startswith(_USER_PREFIX) else ""


@contextmanager
def use_workspace(ctx: WorkspaceContext) -> Iterator[None]:
    """Every transaction begun inside the block runs in `ctx`."""
    token = _ctx.set(ctx)
    try:
        yield
    finally:
        _ctx.reset(token)


def current() -> WorkspaceContext | None:
    return _ctx.get()


@event.listens_for(Session, "after_begin")
def _apply_workspace(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    ctx = _ctx.get()
    connection.execute(
        _APPLY,
        {
            "ws": str(ctx.workspace_id) if ctx else "",
            "actor": str(ctx.actor) if ctx else "system",
            "user_id": _user_id(ctx),
        },
    )


@asynccontextmanager
async def tenant_session(ctx: WorkspaceContext) -> AsyncIterator[AsyncSession]:
    """One transaction as the app role with the workspace applied; commits on exit, rolls
    back on error."""
    with use_workspace(ctx):
        async with db.app_sessionmaker()() as session, session.begin():
            yield session


_ACTOR_NOW = text("SELECT app.current_workspace_id() AS ws, app.current_actor() AS actor")


@asynccontextmanager
async def act_as(session: AsyncSession, actor: ActorRef) -> AsyncIterator[WorkspaceContext]:
    """Inside the block, what `session` writes is `actor`'s: the transaction's actor (and
    `app.user_id`) and the context variable are switched, then put back, in the same
    transaction. For a person's answer that an agent relays (P2-16: the master records a
    reply typed in its chat channel), so it leaves the rows and events the person's own
    answer in the app leaves; whatever the caller writes after the block is the caller's
    again. The workspace stays the transaction's."""
    before = (await session.execute(_ACTOR_NOW)).one()
    if before.ws is None:
        raise RuntimeError("act_as outside a workspace context")
    ctx = WorkspaceContext(before.ws, actor)
    previous = WorkspaceContext(before.ws, ActorRef(str(before.actor)))
    await session.execute(
        _APPLY, {"ws": str(ctx.workspace_id), "actor": str(actor), "user_id": _user_id(ctx)}
    )
    try:
        with use_workspace(ctx):
            yield ctx
    finally:
        await session.execute(
            _APPLY,
            {
                "ws": str(previous.workspace_id),
                "actor": str(previous.actor),
                "user_id": _user_id(previous),
            },
        )


@asynccontextmanager
async def session_for(
    ctx: WorkspaceContext, session: AsyncSession | None
) -> AsyncIterator[AsyncSession]:
    """`session` when the caller passes its own (already in `ctx`'s workspace: the caller
    owns the transaction), else a new `tenant_session(ctx)`. Lets an api function run
    alone or inside a larger transaction (a sync step writing records, cursor and events
    together)."""
    if session is not None:
        yield session
        return
    async with tenant_session(ctx) as own:
        yield own
