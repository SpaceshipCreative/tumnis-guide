"""A plan write runs on the request's own connection (FIX-followups-2, found by SEED).

The routes call each plan write in the request's transaction, which already holds locks
(its Idempotency-Key row, then what it reads and writes). A write that read the day
calendar, the days ahead or the workspace timezone on a second connection (its own
`tenant_session`, or the settings cache's on a miss) waited on that connection inside
the first one's transaction: behind any ACCESS EXCLUSIVE request queued against a table
the first one holds (a TRUNCATE, a migration), that is a deadlock Postgres cannot see,
and the request hangs.

Every write is called here with cold caches (an api process whose caches have not seen
the day, as when the worker built the plan) inside a transaction of the test's own, and
counts the connections opened while it runs. The swap is also run against a waiting lock
request, as SEED found it.

Day: Monday 2026-03-09 in New York (the `workspace` fixture); working hours 09:00 to 18:00
(13:00 to 22:00 UTC) and no events.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid4

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.planning.tests.integration._plan import (
    MONDAY,
    TUESDAY,
    new_project,
    new_task,
    rows,
    until,
    user_ctx,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SWAP_WAIT_S: Final = 5.0  # a swap on one connection takes well under a second
LOCK_TIMEOUT: Final = "15s"  # the waiting lock request gives up after the swap's wait


@dataclass(frozen=True)
class World:
    """One project, a published Monday plan holding `planned` (13:00 to 14:00 UTC) and an
    issue for `unfit` (split 60 + 30, or move to Tuesday), and `spare`, a 30-minute task in
    no plan."""

    planned: UUID
    spare: UUID
    unfit: UUID
    plan_id: UUID
    issue_id: UUID


async def _world(workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls) -> World:
    from tumnis.core.types import Interval  # noqa: PLC0415
    from tumnis.modules.planning import api as planning  # noqa: PLC0415
    from tumnis.modules.planning.rules import (  # noqa: PLC0415
        FitOffer,
        PlannedItem,
        Unplaceable,
    )

    project = await new_project(workspace, clock, "Acme site")
    planned = await new_task(
        workspace, clock, project, "Invoice Acme", label="human", estimate_minutes=60
    )
    unfit = await new_task(
        workspace, clock, project, "Write Acme proposal", label="human", estimate_minutes=90
    )
    spare = await new_task(
        workspace, clock, project, "Book the venue", label="human", estimate_minutes=30
    )
    plan_id = await planning.publish_plan(
        user_ctx(workspace),
        planning.PlanDraft(
            plan_id=uuid4(),
            day=MONDAY,
            trigger="morning",
            source="fallback",
            notice=None,
            fallback_reason=None,
            master_run_id=None,
            profile_version=None,
            built_at=clock.now(),
            items=[
                PlannedItem(
                    task_id=planned.id,
                    position=1,
                    reason="Due soon",
                    block=Interval(
                        datetime(2026, 3, 9, 13, tzinfo=UTC), datetime(2026, 3, 9, 14, tzinfo=UTC)
                    ),
                )
            ],
            issues=[
                Unplaceable(
                    task_id=unfit.id,
                    reason="No gap is big enough",
                    offer=FitOffer(split=[60, 30], move_to=TUESDAY),
                )
            ],
        ),
    )
    [issue] = rows(db, "SELECT id FROM plan_issues WHERE plan_id = %s", plan_id)
    return World(planned.id, spare.id, unfit.id, plan_id, issue["id"])


Write = Callable[["WorkspaceContext", World, "AsyncSession", datetime], Awaitable[Any]]


async def _accept_item(ctx: WorkspaceContext, w: World, s: AsyncSession, now: datetime) -> Any:
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    return await planning.accept_item(ctx, MONDAY, w.planned, now=now, session=s)


async def _accept_all(ctx: WorkspaceContext, w: World, s: AsyncSession, now: datetime) -> Any:
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    return await planning.accept_all(ctx, MONDAY, now=now, session=s)


async def _remove_item(ctx: WorkspaceContext, w: World, s: AsyncSession, now: datetime) -> Any:
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    return await planning.remove_item(ctx, MONDAY, w.planned, now=now, session=s)


async def _swap_item(ctx: WorkspaceContext, w: World, s: AsyncSession, now: datetime) -> Any:
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    body = planning.SwapIn(with_task_id=w.spare)
    return await planning.swap_item(ctx, MONDAY, w.planned, body, now=now, session=s)


async def _split_issue(ctx: WorkspaceContext, w: World, s: AsyncSession, now: datetime) -> Any:
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    return await planning.resolve_issue(ctx, MONDAY, w.issue_id, "split", now=now, session=s)


async def _move_issue(ctx: WorkspaceContext, w: World, s: AsyncSession, now: datetime) -> Any:
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    return await planning.resolve_issue(ctx, MONDAY, w.issue_id, "move", now=now, session=s)


async def _schedule_block(ctx: WorkspaceContext, w: World, s: AsyncSession, now: datetime) -> Any:
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    body = planning.ManualBlockIn(
        block_start=datetime(2026, 3, 9, 15, tzinfo=UTC),
        block_end=datetime(2026, 3, 9, 15, 30, tzinfo=UTC),
    )
    return await planning.schedule_block(ctx, MONDAY, w.spare, body, now=now, session=s)


async def _record_app_open(ctx: WorkspaceContext, w: World, s: AsyncSession, now: datetime) -> Any:
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    return await planning.record_app_open(ctx, now=now, session=s)


def _case(write: Write, req: str, wp: str) -> Any:
    return pytest.param(
        write,
        id=write.__name__.lstrip("_"),
        marks=[
            pytest.mark.req(req),
            pytest.mark.wp(wp),
        ],
    )


@contextlib.contextmanager
def _cold_caches() -> Iterator[None]:
    """A cache backend that has seen nothing: the settings and day calendar entries the
    plan's build left in this process are out of sight, as in a separate api process."""
    from tumnis.core.cache import InProcessCache, use_backend  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    with use_backend(InProcessCache(SystemClock())):
        yield


@contextlib.contextmanager
def _connections_opened() -> Iterator[list[object]]:
    """Every connection the app engine opens in the block (no pool in tests: each session
    connects)."""
    from sqlalchemy import event  # noqa: PLC0415

    from tumnis.core import db as core_db  # noqa: PLC0415

    engine = core_db.app_engine().sync_engine
    opened: list[object] = []

    def on_connect(dbapi_connection: object, _record: object) -> None:
        opened.append(dbapi_connection)

    event.listen(engine, "connect", on_connect)
    try:
        yield opened
    finally:
        event.remove(engine, "connect", on_connect)


@pytest.mark.parametrize(
    "write",
    [
        _case(_accept_item, "J1", "P1-11"),
        _case(_accept_all, "J1", "P1-11"),
        _case(_remove_item, "J1", "P1-11"),
        _case(_swap_item, "J1", "P1-11"),
        _case(_split_issue, "J6", "P1-11"),
        _case(_move_issue, "J6", "P1-11"),
        _case(_schedule_block, "FR-2.6", "P1-12"),
        _case(_record_app_open, "J7", "P1-18"),
    ],
)
async def test_plan_write_opens_no_second_connection(
    write: Write, workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls
) -> None:
    """Called in the request's transaction with cold caches, the write reads and writes on
    that transaction's connection alone: it opens no other."""
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    world = await _world(workspace, clock, db)
    ctx = user_ctx(workspace)
    with _cold_caches():
        async with tenant_session(ctx) as s:
            await s.execute(text("SELECT 1"))  # the request's connection is open
            with _connections_opened() as opened:
                await write(ctx, world, s, clock.now())
    assert opened == []


def _lock_working_hours(db: DbUrls) -> None:
    """Another session's ACCESS EXCLUSIVE request on `working_hours` (as a TRUNCATE or a
    migration makes), granted once the tables' holders commit, then released."""
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'".encode())
        conn.execute(b"LOCK TABLE working_hours IN ACCESS EXCLUSIVE MODE")
        conn.rollback()


def _lock_waiting(db: DbUrls) -> bool:
    found = rows(
        db,
        "SELECT 1 FROM pg_locks WHERE relation = 'working_hours'::regclass "
        "AND mode = 'AccessExclusiveLock' AND NOT granted",
    )
    return bool(found)


@pytest.mark.req("J1")
@pytest.mark.wp("P1-11")
async def test_swap_completes_while_a_lock_request_waits(
    workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls
) -> None:
    """The request's transaction has read the working hours; another session then asks for
    ACCESS EXCLUSIVE on that table and waits behind it. The swap still finishes (within
    SWAP_WAIT_S) with the spare task in the swapped item's place, and the waiting request
    is granted once the request's transaction ends."""
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    world = await _world(workspace, clock, db)
    ctx = user_ctx(workspace)
    with _cold_caches():
        locker: asyncio.Task[None] | None = None
        try:
            async with tenant_session(ctx) as s:
                await s.execute(text("SELECT count(*) FROM working_hours"))
                locker = asyncio.create_task(asyncio.to_thread(_lock_working_hours, db))
                assert await until(lambda: _lock_waiting(db), timeout_s=SWAP_WAIT_S)
                out = await asyncio.wait_for(
                    _swap_item(ctx, world, s, clock.now()), timeout=SWAP_WAIT_S
                )
        finally:
            if locker is not None:
                # Granted once the request's transaction ends; it only times out if the
                # swap held that transaction open past LOCK_TIMEOUT.
                with contextlib.suppress(psycopg.errors.LockNotAvailable):
                    await locker
    live = [item.task_id for item in out.items if item.removed_at is None]
    assert live == [world.spare]


async def _monday_hours_in(ctx: WorkspaceContext, s: AsyncSession, now: datetime) -> None:
    """Monday's working hours set to 10:00 to 12:00 (14:00 to 16:00 UTC) in `s`."""
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    current = await planning.get_working_hours(ctx, session=s)
    body = planning.WorkingHoursIn(
        days=[planning.WorkingDay(weekday=0, start="10:00", end="12:00")],
        version=current.version,
    )
    await planning.put_working_hours(ctx, body, now=now, session=s)


class _RollbackError(Exception):
    """Raised to end a transaction with a rollback."""


@pytest.mark.req("FR-2.6")
@pytest.mark.wp("P1-12")
async def test_day_calendar_in_a_transaction_sees_its_hours(
    workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """With the day already cached, the day calendar read in a transaction that changed
    the working hours shows that transaction's hours, not the cached ones."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    ctx = user_ctx(workspace)
    with _cold_caches():
        cached = await planning.day_calendar(ctx, MONDAY)
        assert cached.window is not None
        assert cached.window.start == datetime(2026, 3, 9, 13, tzinfo=UTC)
        with contextlib.suppress(_RollbackError):
            async with tenant_session(ctx) as s:
                await _monday_hours_in(ctx, s, clock.now())
                seen = await planning.day_calendar(ctx, MONDAY, session=s)
                raise _RollbackError
    assert seen.window is not None
    assert seen.window.start == datetime(2026, 3, 9, 14, tzinfo=UTC)


@pytest.mark.req("FR-2.6")
@pytest.mark.wp("P1-12")
async def test_rolled_back_day_calendar_is_not_cached(
    workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """A day calendar read in a transaction that changed the working hours and then rolled
    back leaves nothing in the cache: the next read shows the committed hours."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    ctx = user_ctx(workspace)
    with _cold_caches():
        with contextlib.suppress(_RollbackError):
            async with tenant_session(ctx) as s:
                await _monday_hours_in(ctx, s, clock.now())
                await planning.day_calendar(ctx, MONDAY, session=s)
                raise _RollbackError
        after = await planning.day_calendar(ctx, MONDAY)
    assert after.window is not None
    assert after.window.start == datetime(2026, 3, 9, 13, tzinfo=UTC)
