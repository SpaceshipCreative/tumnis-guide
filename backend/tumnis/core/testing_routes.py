"""Test-only routes (P0-04), mounted by create_app only when TUMNIS_ADAPTERS=fake; the
preview guard guarantees previews always run with fakes, so real deployments never have
them. `POST /v1/test/reset` empties the database, reloads a seed set and clears a test
clock; `GET /v1/test/requests` lists the last write requests (P0-10); `POST
/v1/test/clock` sets the server clock (A10; issue #6: A0.1 signs in with the TOTP code at
the browser's installed clock, so the server must check it at the same instant); `POST
/v1/test/fakes/{adapter}/script` scripts a fake in every process (R-37, through
tumnis.core.fake_scripts); `GET /v1/test/fakes/runner/last-packet` answers the last `run`
packet the fake runner received and how many (P2-04); `POST /v1/test/tick/{schedule_name}`
fires a registered test tick (R-37, tumnis.core.ticks; P2-15's `focus-wake`)."""

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Final, Self
from uuid import UUID

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tumnis.core import db, deadletter, fake_scripts, ticks
from tumnis.core.clock import OverridableClock
from tumnis.core.errors import ProblemError
from tumnis.core.ratelimit import RateLimiter
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.seed import SEED_PATHS, DatabaseSink, SeedSet, load_seed, writers_registered

router = v1_router("core", prefix="/test", tags=["test"])

# Deployment-level tables a reset keeps: the marker says which deployment this database is.
KEEP_TABLES = frozenset({"deployment_marker"})
# The relay's claim table, locked before every other table (issue #51).
OUTBOX = "outbox"
_LOCK_OUTBOX = text("LOCK TABLE outbox IN ACCESS EXCLUSIVE MODE")
# SQLSTATE deadlock_detected, and how many times a reset runs its TRUNCATE before giving up.
DEADLOCK_DETECTED = "40P01"
DEADLOCK_ATTEMPTS = 3
# A reset that waits this long for one lock is stuck behind a transaction left open (a
# request parked on an `await` with its locks held): it gives up with SQLSTATE
# lock_not_available and reports who holds what (`blocked_report`, SEED) instead of
# holding every later reset until the e2e job's budget runs out.
RESET_LOCK_TIMEOUT_S: Final = 20
LOCK_NOT_AVAILABLE: Final = "55P03"
_SET_LOCK_TIMEOUT = text(f"SET LOCAL lock_timeout = '{RESET_LOCK_TIMEOUT_S}s'")
# The open transactions in this database. As the app role it sees the api's and the
# worker's statements; another role's rows show without them (Postgres hides those).
_OPEN_TRANSACTIONS = text(
    "SELECT pid, usename, application_name, state, wait_event_type, wait_event,"
    " now() - xact_start AS xact_age, pg_blocking_pids(pid) AS blocked_by,"
    " left(query, 300) AS query FROM pg_stat_activity"
    " WHERE datname = current_database() AND pid <> pg_backend_pid()"
    " AND (xact_start IS NOT NULL OR state IS NULL) ORDER BY xact_start"
)
REPORT_TASKS_MAX: Final = 20
REPORT_DETAIL_MAX: Final = 6000
_log = logging.getLogger(__name__)
# The statement-level guards of the append-only tables (pg_trigger.tgtype bit 32).
_TRUNCATE_GUARDS = text(
    "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
    " JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid"
    " WHERE n.nspname = 'public' AND p.proname = 'audit_immutable' AND t.tgtype & 32 <> 0"
)


async def truncate_tables(owner_url: str) -> list[str]:
    """TRUNCATE every table in `public` but the kept ones and Alembic's, as the owner.

    The TRUNCATE locks the tables in name order, so a reader that holds a later table and
    then reads an earlier one closes a lock cycle; Postgres aborts the TRUNCATE (P0-29: a
    load-set reset in CI). The reader finishes once the TRUNCATE gives way, so the reset
    tries again, up to `DEADLOCK_ATTEMPTS` times."""
    engine = create_async_engine(owner_url, poolclass=NullPool)
    attempt = 1
    try:
        while True:
            try:
                return await _truncate_once(engine)
            except DBAPIError as error:
                if getattr(error.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE:
                    report = blocked_report(await _open_transactions(engine), _parked_chains())
                    _log.error("reset blocked on a lock for %ss:\n%s", RESET_LOCK_TIMEOUT_S, report)
                    raise ResetBlockedError(report) from error
                deadlock = getattr(error.orig, "sqlstate", None) == DEADLOCK_DETECTED
                if not deadlock or attempt == DEADLOCK_ATTEMPTS:
                    raise
                attempt += 1
    finally:
        await engine.dispose()


async def _truncate_once(engine: AsyncEngine) -> list[str]:
    async with engine.begin() as conn:
        await conn.execute(_SET_LOCK_TIMEOUT)
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
    return names


class ResetBlockedError(Exception):
    """The reset's TRUNCATE waited RESET_LOCK_TIMEOUT_S for a lock; `report` says who
    holds it (`blocked_report`)."""

    def __init__(self, report: str) -> None:
        super().__init__(report)
        self.report = report


async def _open_transactions(owner: AsyncEngine) -> list[dict[str, Any]]:
    """`_OPEN_TRANSACTIONS` as the app role when this process has one (it sees the api's and
    the worker's statements), else as the owner; empty when neither answers."""
    engines: list[AsyncEngine] = []
    with contextlib.suppress(RuntimeError):  # unconfigured: a harness calls truncate alone
        engines.append(db.app_engine())
    engines.append(owner)
    for engine in engines:
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(_OPEN_TRANSACTIONS)
                return [dict(row._mapping) for row in rows]
        except DBAPIError:
            continue
    return []


def await_chain(task: "asyncio.Task[Any]") -> list[str]:
    """Where `task` is parked: each coroutine from its outermost down its `cr_await` chain,
    as `file:line function`, ending with the awaited future (a task's own stack shows only
    its outermost coroutine)."""
    chain: list[str] = []
    awaited: Any = task.get_coro()
    while awaited is not None:
        frame = (
            getattr(awaited, "cr_frame", None)
            or getattr(awaited, "gi_frame", None)
            or getattr(awaited, "ag_frame", None)
        )
        if frame is None:
            chain.append(repr(awaited)[:200])
            break
        chain.append(f"{frame.f_code.co_filename}:{frame.f_lineno} {frame.f_code.co_name}")
        awaited = (
            getattr(awaited, "cr_await", None)
            or getattr(awaited, "gi_yieldfrom", None)
            or getattr(awaited, "ag_await", None)
        )
    return chain


def _parked_chains() -> list[list[str]]:
    """The await chains of this process's other tasks that run tumnis code."""
    current = asyncio.current_task()
    chains = [
        chain
        for task in asyncio.all_tasks()
        if task is not current
        for chain in [await_chain(task)]
        if any("/tumnis/" in line for line in chain)
    ]
    return chains[:REPORT_TASKS_MAX]


def _one_line(value: Any) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def blocked_report(activity: Sequence[Mapping[str, Any]], chains: Sequence[Sequence[str]]) -> str:
    """A stuck reset's report: each open transaction (pid, role, application, state, wait,
    age, the pids blocking it, its last statement) and each parked task's await chain."""
    lines = [f"open transactions ({len(activity)}):"]
    for row in activity:
        age = row.get("xact_age")
        seconds = f"{age.total_seconds():.0f}s" if isinstance(age, timedelta) else "?"
        lines.append(
            f"  pid {row.get('pid')} {row.get('usename')} app={row.get('application_name')!r}"
            f" {row.get('state')} wait={row.get('wait_event_type')}/{row.get('wait_event')}"
            f" for {seconds} blocked by {list(row.get('blocked_by') or [])}"
            f" last: {_one_line(row.get('query'))}"
        )
    lines.append(f"parked api tasks ({len(chains)}):")
    lines.extend("  " + " -> ".join(chain) for chain in chains)
    return "\n".join(lines)


@router.post("/reset", status_code=204)
@route_policy(
    RoutePolicy(
        auth="none", idempotent=False, not_idempotent_reason="test-only reset of the database"
    )
)
async def reset(
    request: Request,
    seed_set: Annotated[SeedSet, Query(alias="set")] = SeedSet.seed,
    anchor: Annotated[date | None, Query()] = None,
) -> Response:
    """Empties the database and loads `set` (default the seed set) with its dates offset
    from `anchor` (default today in the workspace timezone): the acceptance journeys anchor
    on their Monday, 2026-03-09, whatever day the stack runs."""
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
                    anchor=anchor,
                    clock=state.clock,
                )
    except ResetSupersededError:
        raise HTTPException(status_code=409, detail="superseded by a later reset") from None
    except ResetBlockedError as blocked:
        raise HTTPException(status_code=503, detail=blocked.report[:REPORT_DETAIL_MAX]) from None
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


@router.post("/fakes/{adapter}/script", status_code=204)
@route_policy(
    RoutePolicy(
        auth="none", idempotent=False, not_idempotent_reason="test-only scripting of a fake"
    )
)
async def script_fake(adapter: str, body: Annotated[dict[str, Any], Body()]) -> Response:
    """Stores a script for the fake registered under `adapter` (`decisions.jev`,
    `decisions.vllm`, `generation`), which the fakes of the api and the worker answer from
    until the next `POST /v1/test/reset`. 404 `unknown_fake` for a name with no scriptable
    fake; 422 `invalid_fake_script` for a body that fake cannot read."""
    parse = fake_scripts.parser(adapter)
    if parse is None:
        raise ProblemError(404, "unknown_fake", f"no scriptable fake is named {adapter!r}")
    try:
        key, script = parse(body)
    except ValueError as exc:
        raise ProblemError(422, "invalid_fake_script", str(exc)) from None
    await fake_scripts.put(adapter, key, script)
    return Response(status_code=204)


class LastPacket(BaseModel):
    """The last `run` packet the fake runner received (None before any) and how many it
    received since the last reset."""

    packet: dict[str, Any] | None
    run_messages: int


@router.get("/fakes/runner/last-packet")
@route_policy(RoutePolicy(auth="none"))
async def runner_last_packet() -> LastPacket:
    """What A2.1 reads back (P2-04, R-37): the packet as the fake runner received it, its
    task token live while its run is open (a test-only exception to decision 31; redacted
    when the run ends)."""
    stored = await fake_scripts.last_run_packet()
    if stored is None:
        return LastPacket(packet=None, run_messages=0)
    return LastPacket.model_validate(stored)


class TickOut(BaseModel):
    woken: int  # how many waiting workflows the tick reached


@router.post("/tick/{schedule_name}")
@route_policy(
    RoutePolicy(
        auth="none", idempotent=False, not_idempotent_reason="test-only firing of a schedule"
    )
)
async def fire_tick(request: Request, schedule_name: str) -> TickOut:
    """Fires the test tick named `schedule_name` at the server clock's time (P2-15:
    `focus-wake` wakes every waiting focus workflow, which then reads that time). 404
    `unknown_tick` for a name no module registered."""
    found = ticks.tick(schedule_name)
    if found is None:
        raise ProblemError(404, "unknown_tick", f"no test tick is named {schedule_name!r}")
    woken = await found(deadletter.dbos_client(), request.app.state.clock.now())
    return TickOut(woken=woken)
