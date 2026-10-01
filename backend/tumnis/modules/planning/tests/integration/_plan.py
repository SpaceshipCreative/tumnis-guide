"""Helpers for the daily plan tests (P1-11). No assertions: they make rows through the
modules' apis, wire the master on the fake runner, run the planner and read rows as the
owner, so the code under test may change without touching a locked test body.

- `MONDAY`, `PLAN_TIME`: Monday 2026-03-09 and 08:30 in New York (the `workspace`
  fixture's zone), the default plan time.
- `new_project`, `new_task`, `move_task`: projects' and tasks' apis as the workspace's user.
- `master_on(fake_runner)`: the master profile `tumnis-master` on a connected fake runner.
- `plan_reply(*picks)`: a `plan` skill result picking these tasks, in order.
- `build(workspace_id, day, trigger, now)`: one `build_plan` run in this process.
- `tick(scheduled_time)`: one `planner_tick` in this process.
- `rows(db, query, *params)`: reads as the owner; `published(db)`, `plan_items(db, plan)`,
  `outbox(db, name)`, `build_runs()` (the `build_plan` workflows DBOS knows).
- `until(check)`: polls until `check` returns something truthy.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

MONDAY = date(2026, 3, 9)
TUESDAY = date(2026, 3, 10)
PLAN_TIME = datetime(2026, 3, 9, 12, 30, tzinfo=UTC)  # 08:30 in New York
MASTER = "tumnis-master"
SKILL = "plan"
RUNNER_RECORDINGS = Path(__file__).resolve().parents[5] / "tests/fakes/recordings/runner"
SETTLE_S = 30.0

# Every module's workflows and subscribers, registered before DBOS launches (by name, as
# the composition roots do: a static import would tie this module's tests to all others).
importlib.import_module("tumnis.wiring")


def recorded(name: str) -> Any:
    """A scripted runner result (`tests/fakes/recordings/runner/<name>.result.json`)."""
    return json.loads((RUNNER_RECORDINGS / f"{name}.result.json").read_text())


def rows(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query.encode(), params).fetchall())


def execute(db: DbUrls, query: str, *params: Any) -> None:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(query.encode(), params)


def plans(db: DbUrls) -> list[dict[str, Any]]:
    """Every daily plan, oldest build first."""
    return rows(db, "SELECT * FROM daily_plans ORDER BY built_at, created_at")


def published(db: DbUrls, day: date = MONDAY) -> dict[str, Any] | None:
    found = rows(db, "SELECT * FROM daily_plans WHERE day = %s AND status = 'published'", day)
    return found[0] if found else None


def plan_items(db: DbUrls, plan_id: UUID) -> list[dict[str, Any]]:
    return rows(db, "SELECT * FROM plan_items WHERE plan_id = %s ORDER BY position", plan_id)


def outbox(db: DbUrls, name: str) -> list[dict[str, Any]]:
    return rows(db, "SELECT * FROM outbox WHERE name = %s ORDER BY id", name)


def build_runs() -> list[Any]:
    """The `build_plan` workflows DBOS has started (any status). DBOS refuses its sync call
    inside a running event loop (these tests are async), so it runs on a thread of its own."""
    from dbos import DBOS  # noqa: PLC0415

    with ThreadPoolExecutor(max_workers=1) as pool:
        return list(pool.submit(DBOS.list_workflows, name="build_plan").result())


async def until(
    check: Callable[[], Any], *, timeout_s: float = SETTLE_S, poll_s: float = 0.1
) -> Any:
    """The first truthy value of `check`, or its last value once `timeout_s` has passed."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = check()
        if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
            value = await value
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(poll_s)


def user_ctx(workspace: WorkspaceHandle) -> Any:
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415

    return WorkspaceContext(workspace.id, ActorRef(f"user:{workspace.user_id}"))


async def new_project(workspace: WorkspaceHandle, clock: FixedClock, name: str) -> UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ctx = user_ctx(workspace)
    async with tenant_session(ctx) as s:
        made = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name=name), now=clock.now()
        )
    return made.id


async def new_task(
    workspace: WorkspaceHandle, clock: FixedClock, project_id: UUID, title: str, **fields: Any
) -> Any:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    ctx = user_ctx(workspace)
    async with tenant_session(ctx) as s:
        return await tasks.create_task(
            s,
            ctx.actor,
            tasks.TaskCreate(project_id=project_id, title=title, **fields),
            now=clock.now(),
        )


async def get_task(workspace: WorkspaceHandle, task_id: UUID) -> Any:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    async with tenant_session(user_ctx(workspace)) as s:
        return await tasks.get_task(s, task_id)


async def move_task(
    workspace: WorkspaceHandle, clock: FixedClock, task_id: UUID, *path: str, agent: bool = False
) -> Any:
    """Walk the task through these statuses, as the user (or an agent's key for the edges
    only an agent takes, such as In progress -> Waiting on human)."""
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    task = await get_task(workspace, task_id)
    for to in path:
        actor = (
            ActorRef("api_key:0199aa00-0000-7000-8000-00000000a6e7")
            if agent and to == "waiting_on_human"
            else ActorRef(f"user:{workspace.user_id}")
        )
        async with tenant_session(WorkspaceContext(workspace.id, actor)) as s:
            task = await tasks.change_status(
                s, actor, task_id, tasks.Status(to), task.version, now=clock.now()
            )
    return task


async def set_monday_hours(
    workspace: WorkspaceHandle, clock: FixedClock, start: str, end: str
) -> None:
    """Monday's working hours (P1-10), the other days left at their default."""
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    ctx = user_ctx(workspace)
    current = await planning.get_working_hours(ctx)
    await planning.put_working_hours(
        ctx,
        planning.WorkingHoursIn(
            days=[planning.WorkingDay(weekday=0, start=start, end=end)], version=current.version
        ),
        now=clock.now(),
    )


def master_on(fake_runner: FakeRunnerFactory) -> FakeRunner:
    """A connected runner carrying the master profile (`tumnis-master`, role master)."""
    runner = fake_runner(profiles=[MASTER])
    fake_runner.register_profile(MASTER, runner=runner, role="master")
    return runner


def plan_reply(*picks: tuple[UUID | str, str], alternates: tuple[UUID, ...] = ()) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "picks": [{"task_id": str(task_id), "reason": reason} for task_id, reason in picks],
        "alternates": [str(a) for a in alternates],
        "notes": None,
    }


async def build(workspace_id: UUID, day: date, trigger: str, now: datetime) -> Any:
    """One `build_plan` in this process (the `dbos` fixture); its result (the plan id)."""
    from dbos import DBOS  # noqa: PLC0415

    from tumnis.modules.planning import workflows  # noqa: PLC0415

    handle = await DBOS.start_workflow_async(workflows.build_plan, workspace_id, day, trigger, now)
    return await handle.get_result()


async def tick(scheduled_time: datetime) -> None:
    """One `planner_tick` at `scheduled_time` in this process, as the schedule runs it."""
    from dbos import DBOS  # noqa: PLC0415

    from tumnis.modules.planning import workflows  # noqa: PLC0415

    handle = await DBOS.start_workflow_async(workflows.planner_tick, scheduled_time, None)
    await handle.get_result()


async def builds_settled() -> bool:
    """Every `build_plan` DBOS knows has finished."""
    return all(w.status in {"SUCCESS", "ERROR"} for w in build_runs())


async def relay() -> None:
    from tumnis.core.events import relay_once  # noqa: PLC0415

    await relay_once()
