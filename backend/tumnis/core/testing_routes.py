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
import random
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Final, Self
from uuid import UUID

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tumnis.core import db, deadletter, fake_scripts, ticks
from tumnis.core.backoff import full_jitter
from tumnis.core.clock import OverridableClock
from tumnis.core.errors import ProblemError
from tumnis.core.ratelimit import RateLimiter
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.testing_writes import WritesInFlight
from tumnis.seed import SEED_PATHS, DatabaseSink, SeedSet, load_seed, writers_registered

router = v1_router("core", prefix="/test", tags=["test"])

# Deployment-level tables a reset keeps: the marker says which deployment this database is.
KEEP_TABLES = frozenset({"deployment_marker"})
# The table every writer locks last: a module writes its rows, then emits into `outbox` in
# the same transaction. The TRUNCATE locks it last too (APP-F03).
OUTBOX = "outbox"
# SQLSTATE deadlock_detected, and how many times a reset runs its TRUNCATE before giving up.
# Between attempts it pauses a full-jitter moment (base DEADLOCK_PAUSE_BASE_S, at most
# DEADLOCK_PAUSE_CAP_S), so the transaction it collided with commits before the next
# TRUNCATE queues behind it again. Since the reset gives way before Postgres looks for a
# deadlock (RESET_LOCK_TIMEOUT_S), this is a backstop (APP-04, APP-F03).
DEADLOCK_DETECTED = "40P01"
DEADLOCK_ATTEMPTS = 10
DEADLOCK_PAUSE_BASE_S: Final = 0.05
DEADLOCK_PAUSE_CAP_S: Final = 1.0
# The TRUNCATE never waits long for a lock while it holds others (APP-F03). Two kinds of
# transaction take their locks in another order than the TRUNCATE's: one that holds a
# later table and then reads an earlier one (a cycle Postgres sees), and one that holds a
# table while it waits, in Python, on its own second connection that queues behind the
# TRUNCATE (a cycle Postgres cannot see: the relay holds its `outbox` claim while it reads
# `module_flags` (issue #51), J1's swap reads the day calendar). Each lock the TRUNCATE
# waits for is bounded by RESET_LOCK_TIMEOUT_S, below the server's `deadlock_timeout`
# (1 s by default), so the TRUNCATE gives way first (SQLSTATE lock_not_available) and
# Postgres never picks it, or a request it blocked, as a deadlock victim. The reset logs
# who held what (`blocked_report`), pauses, and tries again until LOCK_WAIT_BUDGET_S have
# gone by since it began (by the event loop's monotonic clock, pauses included; no pause
# runs past that deadline); then it answers 503 with the report (SEED).
RESET_LOCK_TIMEOUT_S: float = 0.25  # read per attempt (a test changes it)
LOCK_WAIT_BUDGET_S: Final = 20.0
LOCK_NOT_AVAILABLE: Final = "55P03"
_SET_LOCK_TIMEOUT = text("SELECT set_config('lock_timeout', :timeout, true)")
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

    The TRUNCATE locks the tables in name order and `outbox` last, the order of every
    writer that emits (APP-F03). A transaction that takes its locks in another order and
    holds one the TRUNCATE waits for makes it give way at RESET_LOCK_TIMEOUT_S: the reset
    logs the open transactions, pauses a jittered moment and tries again, until
    LOCK_WAIT_BUDGET_S have gone by since it began, then raises ResetBlockedError. A
    deadlock Postgres reports anyway (P0-29, APP-04) is tried again too, up to
    `DEADLOCK_ATTEMPTS` times."""
    engine = create_async_engine(owner_url, poolclass=NullPool)
    attempt = lock_waits = 1
    deadline = _monotonic() + LOCK_WAIT_BUDGET_S
    try:
        while True:
            try:
                return await _truncate_once(engine)
            except DBAPIError as error:
                code = getattr(error.orig, "sqlstate", None)
                if code == LOCK_NOT_AVAILABLE:
                    report = blocked_report(await _open_transactions(engine), _parked_chains())
                    left = deadline - _monotonic()
                    _log.warning(
                        "reset blocked on a lock for %ss (give-way %d, %.1fs of %.0fs left):\n%s",
                        RESET_LOCK_TIMEOUT_S,
                        lock_waits,
                        max(left, 0.0),
                        LOCK_WAIT_BUDGET_S,
                        report,
                    )
                    if left <= 0:
                        raise ResetBlockedError(report) from error
                    await _deadlock_pause(min(_jitter(lock_waits), left))
                    lock_waits += 1
                    continue
                if code != DEADLOCK_DETECTED or attempt == DEADLOCK_ATTEMPTS:
                    raise
                _log.info("reset lost a deadlock (attempt %d of %d)", attempt, DEADLOCK_ATTEMPTS)
                await _deadlock_pause(_jitter(attempt))
                attempt += 1
    finally:
        await engine.dispose()


def _jitter(attempt: int) -> float:
    return full_jitter(
        attempt,
        base=DEADLOCK_PAUSE_BASE_S,
        cap=DEADLOCK_PAUSE_CAP_S,
        rand=random.random,  # jitter, not a secret
    )


def _monotonic() -> float:
    """Seconds on the event loop's monotonic clock, for the reset's own deadline (a test
    replaces it)."""
    return asyncio.get_running_loop().time()


async def _deadlock_pause(seconds: float) -> None:
    """The pause before a reset tries again after a deadlock or a give-way (a test
    replaces it)."""
    await asyncio.sleep(seconds)


async def _truncate_once(engine: AsyncEngine) -> list[str]:
    async with engine.begin() as conn:
        await conn.execute(_SET_LOCK_TIMEOUT, {"timeout": f"{int(RESET_LOCK_TIMEOUT_S * 1000)}ms"})
        names = [
            name
            for (name,) in await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            if name not in KEEP_TABLES and not name.startswith("alembic_version")
        ]
        if names:
            quote = conn.dialect.identifier_preparer.quote
            # APP-F03: the TRUNCATE locks its tables in the order it lists them, and a
            # writer locks its own rows' table before `outbox` (it emits last). `outbox`
            # first (issue #51) was the opposite order: a writer holding `tasks` waited for
            # `outbox` while the reset held it and waited for `tasks`, 125 deadlocks in
            # one e2e run. Listed last, `outbox` is free until the reset holds everything
            # else, so such a writer commits, and later writers queue behind the reset.
            # The relay, which holds its `outbox` claim while it reads other tables
            # (issue #51), is the one left in another order; the short lock wait settles
            # it (RESET_LOCK_TIMEOUT_S).
            ordered = sorted(name for name in names if name != OUTBOX)
            ordered += [OUTBOX] if OUTBOX in names else []
            listed = ", ".join(quote(name) for name in ordered)
            # The append-only tables (P0-15) refuse TRUNCATE by trigger, the owner's
            # included; a reset empties their chains with everything else, the guards
            # off only inside this transaction. ALTER TABLE locks them (SHARE ROW
            # EXCLUSIVE) before the TRUNCATE does, in a fixed order; the short lock wait
            # bounds a collision there too.
            guards = sorted((await conn.execute(_TRUNCATE_GUARDS)).all())
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
    """The reset's TRUNCATE kept giving way at its lock timeout for LOCK_WAIT_BUDGET_S;
    `report` says who held what the last time (`blocked_report`)."""

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
            # The TRUNCATE removed the world, so every workflow still outstanding now
            # works for it; the seed's own workflows start after (SEED, T-SEED-29, APP-16).
            await cancel_removed_workflows()
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


# DBOS statuses of a workflow not yet done (DBOS's own `workflow_is_active`).
OUTSTANDING: Final = ["ENQUEUED", "PENDING", "DELAYED"]


async def cancel_removed_workflows() -> int:
    """Cancel every workflow still queued, delayed or running, whatever its name, except a
    schedule's own runs; how many (SEED, T-SEED-29; APP-16).

    A reset empties the tables, not DBOS. A workflow of the removed world fails on its
    missing rows or, worse, writes into the new one: event deliveries backed off holding the
    events queue's slots, so the next test's `run.requested` waited behind them for over a
    minute (A2.1, A2.2); notification deliveries, labelling and plan steps failed on foreign
    keys into `workspaces` (APP-16); a focus workflow still waiting heard the next test's
    `focus-wake` ticks. Cancelling takes a queued or delayed workflow off its queue at once
    (DBOS sets it CANCELLED and clears its queue) and stops a running one at its next step
    (DBOS: "interrupting it at the beginning of its next step"); one parked in `recv` or a
    backoff sleep ends when it wakes. A run or a build also has its own way to end
    (T-SEED-23, T-SEED-24).

    It runs after the TRUNCATE and before the seed loads, so everything outstanding is the
    removed world's. A schedule's runs (`schedule_name` set, DBOS `apply_schedules`) are
    left alone: the schedule belongs to the deployment and fires again for the new world.

    Only in the compose.test shape, where the api serves the fake-script store: a route
    test's app runs no workflows and may have no DBOS system database to ask (or one with
    no DBOS tables yet)."""
    if not (fake_scripts.enabled() and deadletter.dbos_configured()):
        return 0
    client = deadletter.dbos_client()
    try:
        outstanding = await client.list_workflows_async(
            status=OUTSTANDING, load_input=False, load_output=False
        )
    except DBAPIError as error:
        _log.info("reset: no DBOS system database to sweep (%s)", type(error.orig).__name__)
        return 0
    ids = [w.workflow_id for w in outstanding if not w.schedule_name]
    if ids:
        await client.cancel_workflows_async(ids)
        _log.info("reset: cancelled %d workflows of the removed world", len(ids))
    return len(ids)


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


async def writes_settled(request: Request) -> None:
    """A clock change first lets the writes already being served finish (A2.6, J8): one
    that arrived before it, but had not read the clock yet, keeps the time it came at."""
    writes = getattr(request.app.state, "writes_in_flight", None)
    if isinstance(writes, WritesInFlight):
        await writes.settle()


@router.post("/clock", dependencies=[Depends(writes_settled)])
@route_policy(
    RoutePolicy(
        auth="none", idempotent=False, not_idempotent_reason="test-only control of the clock"
    )
)
async def set_clock(request: Request, body: ClockIn) -> ClockOut:
    """Fixes the server clock (every route and TOTP checks read it; rate limits keep real
    time) until the next `POST /v1/test/reset`. The Playwright fixtures call it when a test installs
    `page.clock`, so both clocks show the same instant. The instant is also stored for the
    worker, which stamps a run's end with it (`fake_scripts.worker_now`, decision 86)."""
    clock = request.app.state.clock
    if not isinstance(clock, OverridableClock):  # create_app wraps it whenever fakes are on
        raise HTTPException(status_code=500, detail="the app clock cannot be overridden")
    if body.time is not None:
        now = clock.set(body.time)
    else:
        now = clock.advance(timedelta(seconds=body.advance_seconds or 0))
    await fake_scripts.store_fixed_clock(now)
    return ClockOut(now=now)


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
