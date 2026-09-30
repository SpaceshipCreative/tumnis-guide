"""Test-only routes (P0-04), mounted by create_app only when TUMNIS_ADAPTERS=fake; the
preview guard guarantees previews always run with fakes, so real deployments never have
them. `POST /v1/test/reset` empties the database, reloads a seed set and clears a test
clock; `GET /v1/test/requests` lists the last write requests (P0-10); `POST
/v1/test/clock` sets the server clock (A10; issue #6: A0.1 signs in with the TOTP code at
the browser's installed clock, so the server must check it at the same instant)."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tumnis.core.clock import OverridableClock
from tumnis.core.ratelimit import RateLimiter
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.seed import SEED_PATHS, DatabaseSink, SeedSet, load_seed, writers_registered

router = v1_router("core", prefix="/test", tags=["test"])

# Deployment-level tables a reset keeps: the marker says which deployment this database is.
KEEP_TABLES = frozenset({"deployment_marker"})
# The relay's claim table, locked before every other table (issue #51).
OUTBOX = "outbox"
_LOCK_OUTBOX = text("LOCK TABLE outbox IN ACCESS EXCLUSIVE MODE")
# The statement-level guards of the append-only tables (pg_trigger.tgtype bit 32).
_TRUNCATE_GUARDS = text(
    "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
    " JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid"
    " WHERE n.nspname = 'public' AND p.proname = 'audit_immutable' AND t.tgtype & 32 <> 0"
)


async def truncate_tables(owner_url: str) -> list[str]:
    """TRUNCATE every table in `public` but the kept ones and Alembic's, as the owner."""
    engine = create_async_engine(owner_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            names = [
                name
                for (name,) in await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
                if name not in KEEP_TABLES and not name.startswith("alembic_version")
            ]
            if names:
                quote = conn.dialect.identifier_preparer.quote
                listed = ", ".join(quote(name) for name in sorted(names))
                # Issue #51: `outbox` first, before any other lock. A relay pass holds its
                # claim on `outbox` while it reads `module_flags` on another connection;
                # a TRUNCATE that took `module_flags` first and then waited for the claim
                # deadlocked with it, unseen by Postgres. Waiting here holds nothing the
                # relay needs, and a claim that starts later waits for the reset.
                if OUTBOX in names:
                    await conn.execute(_LOCK_OUTBOX)
                # The append-only tables (P0-15) refuse TRUNCATE by trigger, the owner's
                # included; a reset empties their chains with everything else, the guards
                # off only inside this transaction.
                guards = (await conn.execute(_TRUNCATE_GUARDS)).all()
                for table, trigger in guards:
                    await conn.execute(
                        # nosemgrep: tumnis-sql-fstring  # identifiers, quoted by the dialect
                        text(f"ALTER TABLE {quote(table)} DISABLE TRIGGER {quote(trigger)}")
                    )
                # nosemgrep: tumnis-sql-fstring  # `listed` is quoted identifiers only
                await conn.execute(text(f"TRUNCATE {listed} RESTART IDENTITY CASCADE"))
                for table, trigger in guards:
                    await conn.execute(
                        # nosemgrep: tumnis-sql-fstring  # identifiers, quoted by the dialect
                        text(f"ALTER TABLE {quote(table)} ENABLE TRIGGER {quote(trigger)}")
                    )
    finally:
        await engine.dispose()
    return names


@router.post("/reset", status_code=204)
@route_policy(
    RoutePolicy(
        auth="none", idempotent=False, not_idempotent_reason="test-only reset of the database"
    )
)
async def reset(
    request: Request, seed_set: Annotated[SeedSet, Query(alias="set")] = SeedSet.seed
) -> Response:
    settings = request.app.state.settings
    if settings.database_owner_url is None:
        raise HTTPException(status_code=500, detail="reset needs DATABASE_OWNER_URL")
    # Issue #56: the latest reset wins. A reset whose caller gave up keeps seeding (A0.6's
    # `load` set outlives its test); the next reset's TRUNCATE ran under that seed, and
    # waiting for it outlasts the next test. The newer reset supersedes it instead: the
    # stale seed stops at its next record, and the lock keeps the two apart until then.
    state = request.app.state
    state.reset_generation = generation = getattr(state, "reset_generation", 0) + 1
    try:
        async with _reset_lock(request.app):
            _refuse_if_superseded(state, generation)
            await truncate_tables(settings.database_owner_url)
            clock = state.clock
            if isinstance(clock, OverridableClock):
                clock.clear()  # a fresh stack reads the real time again
            # A fresh stack: rate-limit buckets start full again (P0-13: every e2e test
            # signs in from the same address, which `login` would otherwise throttle).
            if isinstance(getattr(state, "rate_limiter", None), RateLimiter):
                state.rate_limiter = RateLimiter(state.clock)
            if writers_registered():  # from P0-17 on; before that the seed has nowhere to go
                await load_seed(
                    SEED_PATHS[seed_set],
                    _ResetSink(lambda: _refuse_if_superseded(state, generation)),
                    clock=state.clock,
                )
    except ResetSupersededError:
        raise HTTPException(status_code=409, detail="superseded by a later reset") from None
    return Response(status_code=204)


class ResetSupersededError(Exception):
    """A later `POST /v1/test/reset` arrived; this one stops where it is."""


def _refuse_if_superseded(state: Any, generation: int) -> None:
    if state.reset_generation != generation:
        raise ResetSupersededError


class _ResetSink(DatabaseSink):
    """The reset's seed sink: checks before each record that no later reset has begun."""

    def __init__(self, check: Callable[[], None]) -> None:
        super().__init__(skip_missing=True)
        self._check = check

    async def _write(self, kind: str, *args: Any) -> UUID:
        self._check()
        return await super()._write(kind, *args)


def _reset_lock(app: FastAPI) -> asyncio.Lock:
    """The app's reset lock, made on first use (in the app's event loop)."""
    lock: asyncio.Lock | None = getattr(app.state, "reset_lock", None)
    if lock is None:
        lock = app.state.reset_lock = asyncio.Lock()
    return lock


class RecordedRequest(BaseModel):
    method: str
    path: str
    route: str
    idempotency_key: str | None
    status: int
    replayed: bool


class RecordedRequests(BaseModel):
    items: list[RecordedRequest]


@router.get("/requests")
@route_policy(RoutePolicy(auth="none"))
async def recorded_requests(request: Request) -> RecordedRequests:
    """The last 500 (plan default) write requests, oldest first: method, path, route,
    idempotency key, status and whether they replayed (A0.2)."""
    log: Any = getattr(request.app.state, "request_log", ())
    return RecordedRequests(items=[RecordedRequest.model_validate(entry) for entry in log])


class ClockIn(BaseModel):
    """Exactly one of `time` (an aware ISO-8601 instant the clock is fixed at) or
    `advance_seconds` (moves the fixed instant; fixes an unset clock at now first)."""

    time: AwareDatetime | None = None
    advance_seconds: float | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.time is None) == (self.advance_seconds is None):
            raise ValueError("give exactly one of time or advance_seconds")
        return self


class ClockOut(BaseModel):
    now: datetime


@router.post("/clock")
@route_policy(
    RoutePolicy(
        auth="none", idempotent=False, not_idempotent_reason="test-only control of the clock"
    )
)
async def set_clock(request: Request, body: ClockIn) -> ClockOut:
    """Fixes the server clock (every route, TOTP checks and rate limits read it) until the
    next `POST /v1/test/reset`. The Playwright fixtures call it when a test installs
    `page.clock`, so both clocks show the same instant."""
    clock = request.app.state.clock
    if not isinstance(clock, OverridableClock):  # create_app wraps it whenever fakes are on
        raise HTTPException(status_code=500, detail="the app clock cannot be overridden")
    if body.time is not None:
        return ClockOut(now=clock.set(body.time))
    return ClockOut(now=clock.advance(timedelta(seconds=body.advance_seconds or 0)))
