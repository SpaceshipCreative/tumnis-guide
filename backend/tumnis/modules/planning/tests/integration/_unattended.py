"""Helpers for the unattended window tests (P4-04). No assertions: they make rows through
the modules' apis, fire the tick and read rows as the owner, so the code under test may
change without touching a locked test body. Everything under `tumnis.modules` that P4-04
adds is reached inside the functions, so the tests collect before it exists.

- `MONDAY_NIGHT`: 22:05 on Monday 2026-03-09 in New York (the `workspace` fixture's zone,
  EDT), five minutes into a 22:00 to 06:00 window; `WINDOW_END` is Tuesday 06:00 EDT.
- `set_window(workspace, ...)`: the workspace's window (or a project's override).
- `ai_task(workspace, clock, project, title, ...)`: an AI task in Today with acceptance
  criteria; `taint(db, task)` marks it made from outside content (SAF-1).
- `queue(workspace, clock, task)`: queue it for the window as the user.
- `RunSpy`: stands in for `agents.api.request_run` and records each call.
- `tick(now)`: one tick in this process, as the test route fires it; `tick_workflow(now)`:
  one run of the scheduled `unattended_tick` workflow.
- `pause_project(workspace, clock, project)`: P2-09's project pause.
- `max_run_minutes(workspace, project)`: the project policy's cap (SAF-5).
"""

from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tumnis.modules.planning.tests.integration._plan import execute, rows, user_ctx

if TYPE_CHECKING:
    import pytest

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

MONDAY_NIGHT = datetime(2026, 3, 10, 2, 5, tzinfo=UTC)  # 22:05 EDT, Monday 2026-03-09
WINDOW_START = datetime(2026, 3, 10, 2, 0, tzinfo=UTC)  # 22:00 EDT
WINDOW_END = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)  # Tuesday 06:00 EDT
TUESDAY_NIGHT = MONDAY_NIGHT + timedelta(days=1)
WEEKDAYS = [0, 1, 2, 3, 4]
CRITERIA = "The footer link opens the right page"


def _planning() -> Any:
    return importlib.import_module("tumnis.modules.planning.api")


def _tasks() -> Any:
    return importlib.import_module("tumnis.modules.tasks.api")


async def set_window(
    workspace: WorkspaceHandle,
    clock: FixedClock,
    *,
    weekdays: list[int] | None = None,
    start: time = time(22),
    end: time = time(6),
    project_id: UUID | None = None,
) -> Any:
    """The window (Monday to Friday 22:00 to 06:00 unless told otherwise) for the workspace,
    or for one project when `project_id` is given."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    planning = _planning()
    ctx = user_ctx(workspace)
    current = await planning.get_unattended_window(ctx, project_id)
    body = planning.UnattendedWindowIn(
        project_id=project_id,
        window=planning.WindowSpec(
            weekdays=weekdays if weekdays is not None else WEEKDAYS,
            start_local=start,
            end_local=end,
        ),
        version=current.version,
    )
    async with tenant_session(ctx) as s:
        return await planning.put_unattended_window(ctx, body, now=clock.now(), session=s)


async def ai_task(
    workspace: WorkspaceHandle,
    clock: FixedClock,
    project_id: UUID,
    title: str,
    *,
    label: str = "ai",
    criteria: str | None = CRITERIA,
    status: str = "today",
) -> UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    tasks = _tasks()
    ctx = user_ctx(workspace)
    async with tenant_session(ctx) as s:
        made = await tasks.create_task(
            s,
            ctx.actor,
            tasks.TaskCreate(
                project_id=project_id,
                title=title,
                label=label,
                status=status,
                first_action="Open the footer component",
                acceptance_criteria=criteria,
            ),
            now=clock.now(),
        )
    return UUID(str(made.id))


def taint(db: DbUrls, task_id: UUID) -> None:
    """Mark the task made from outside content, as P2-08's propagation leaves it."""
    execute(db, "UPDATE tasks SET tainted = true WHERE id = %s", task_id)


async def queue(
    workspace: WorkspaceHandle, clock: FixedClock, task_id: UUID, *, queued: bool = True
) -> Any:
    """Queue (or unqueue) the task for unattended running, as the user."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    tasks = _tasks()
    ctx = user_ctx(workspace)
    async with tenant_session(ctx) as s:
        return await tasks.queue_unattended(s, ctx.actor, task_id, queued=queued, now=clock.now())


@dataclass
class RunSpy:
    """`agents.api.request_run` replaced: records (task_id, kind, unattended) per call and
    answers a fresh run id; `refuse` makes it answer that 409 code instead."""

    calls: list[tuple[UUID, str, bool]] = field(default_factory=list)
    refuse: str | None = None

    async def __call__(self, task_id: UUID, kind: Any, **kwargs: Any) -> UUID:
        from tumnis.core.errors import ProblemError  # noqa: PLC0415

        if self.refuse is not None:
            raise ProblemError(409, self.refuse, "refused by the spy")
        kind_name = str(getattr(kind, "value", kind))
        self.calls.append((task_id, kind_name, bool(kwargs.get("unattended"))))
        return uuid.uuid4()

    def tasks(self) -> list[UUID]:
        return [task for task, _, _ in self.calls]


def spy_runs(monkeypatch: pytest.MonkeyPatch) -> RunSpy:
    spy = RunSpy()
    agents = importlib.import_module("tumnis.modules.agents.api")
    monkeypatch.setattr(agents, "request_run", spy)
    return spy


async def tick(now: datetime) -> Any:
    """One unattended tick at `now`, in this process (as `POST /v1/test/tick/unattended-tick`
    fires it)."""
    return await _planning().run_unattended_tick(now)


async def tick_workflow(now: datetime) -> Any:
    """One run of the scheduled `unattended_tick` workflow at `now`."""
    workflows = importlib.import_module("tumnis.modules.planning.workflows")
    return await workflows.unattended_tick(now, None)


async def pause_project(workspace: WorkspaceHandle, clock: FixedClock, project_id: UUID) -> None:
    agents = importlib.import_module("tumnis.modules.agents.api")
    await agents.pause(
        user_ctx(workspace),
        agents.PauseIn(scope="project", project_id=project_id, reason="test"),
        now=clock.now(),
    )


async def max_run_minutes(workspace: WorkspaceHandle, project_id: UUID) -> int:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    async with tenant_session(user_ctx(workspace)) as s:
        return int((await projects.get_policy(s, project_id)).max_run_minutes)


def review_items(db: DbUrls, kind: str, task_id: UUID | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM review_items WHERE kind = %s AND deleted_at IS NULL"
    params: list[Any] = [kind]
    if task_id is not None:
        query += " AND target_id = %s"
        params.append(task_id)
    return rows(db, query + " ORDER BY created_at, id", *params)


def queued_at(db: DbUrls, task_id: UUID) -> datetime | None:
    [row] = rows(db, "SELECT unattended_queued_at FROM tasks WHERE id = %s", task_id)
    found: datetime | None = row["unattended_queued_at"]
    return found
