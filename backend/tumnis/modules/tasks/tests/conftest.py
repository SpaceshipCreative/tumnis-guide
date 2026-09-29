"""Fixtures for the tasks tests (P0-18); the shared ones (app, clock, db, workspace,
session_client, key_client) come from backend/tests/fixtures.

- `actors`: the ActorRefs a test writes as: the workspace's user (a human), an API key
  (an agent: any key or task token, A13) and the system.
- `make_project(**overrides)`: a project made through `projects.api.create_project`.
- `make_task(**overrides)`: a task made through `tasks.api.create_task` (a human writes
  it unless `actor=` names "agent" or "system"); without `project_id` it lands in one
  project made for the test.
- `make_subtask(parent, **overrides)`: a task under `parent`, in its project.
- `set_status(task, to, actor="human")`: `tasks.api.change_status` at the task's version.
- `outbox(db, name)`: the payloads of that event's outbox rows, read as the owner.
- `tz_workspace(tz, at=None)`: sets the workspace timezone at a given instant (P0-19).
- `drain_workflows()`: waits until the DBOS workflows a tick enqueued have run (P0-19).

The fixtures are plain (sync) and return coroutine functions: engines keep no idle
connections (NullPool), so a test may drive them from any event loop.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from datetime import datetime

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.core.types import ActorRef
    from tumnis.modules.projects.api import ProjectOut
    from tumnis.modules.tasks.api import TaskOut


@dataclass(frozen=True)
class Actors:
    human: ActorRef
    agent: ActorRef
    system: ActorRef

    def of(self, who: str) -> ActorRef:
        """ "human", "agent" or "system", or an ActorRef as it is."""
        from tumnis.core.types import ActorRef  # noqa: PLC0415

        named = {"human": self.human, "agent": self.agent, "system": self.system}
        return named.get(who, ActorRef(who))


@pytest.fixture
def actors(db: DbUrls, workspace: WorkspaceHandle) -> Actors:
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR, ActorRef  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    return Actors(
        human=ActorRef(f"user:{workspace.user_id}"),
        agent=ActorRef(f"api_key:{uuid.uuid4()}"),
        system=SYSTEM_ACTOR,
    )


MakeProject = Callable[..., Awaitable["ProjectOut"]]
MakeTask = Callable[..., Awaitable["TaskOut"]]
MakeSubtask = Callable[..., Awaitable["TaskOut"]]
SetStatus = Callable[..., Awaitable["TaskOut"]]


@pytest.fixture
def make_project(workspace: WorkspaceHandle, actors: Actors, clock: FixedClock) -> MakeProject:
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    counter = itertools.count(1)

    async def make(**overrides: Any) -> ProjectOut:
        overrides.setdefault("name", f"Tasks project {next(counter)}")
        async with tenant_session(WorkspaceContext(workspace.id, actors.human)) as s:
            return await projects.create_project(
                s, actors.human, projects.ProjectCreate(**overrides), now=clock.now()
            )

    return make


@pytest.fixture
def make_task(
    workspace: WorkspaceHandle, actors: Actors, clock: FixedClock, make_project: MakeProject
) -> MakeTask:
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api  # noqa: PLC0415

    counter = itertools.count(1)
    default_project: list[uuid.UUID] = []

    async def make(*, actor: str = "human", **overrides: Any) -> TaskOut:
        if "project_id" not in overrides:
            if not default_project:
                default_project.append((await make_project()).id)
            overrides["project_id"] = default_project[0]
        overrides.setdefault("title", f"Task {next(counter)}")
        who = actors.of(actor)
        async with tenant_session(WorkspaceContext(workspace.id, who)) as s:
            return await api.create_task(s, who, api.TaskCreate(**overrides), now=clock.now())

    return make


@pytest.fixture
def make_subtask(make_task: MakeTask) -> MakeSubtask:
    async def make(parent: TaskOut, **overrides: Any) -> TaskOut:
        return await make_task(project_id=parent.project_id, parent_id=parent.id, **overrides)

    return make


@pytest.fixture
def set_status(workspace: WorkspaceHandle, actors: Actors, clock: FixedClock) -> SetStatus:
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api  # noqa: PLC0415

    async def change(task: TaskOut, to: str, actor: str = "human") -> TaskOut:
        who = actors.of(actor)
        async with tenant_session(WorkspaceContext(workspace.id, who)) as s:
            return await api.change_status(
                s, who, task.id, api.Status(to), task.version, now=clock.now()
            )

    return change


def owner_rows(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


def outbox(db: DbUrls, name: str) -> list[dict[str, Any]]:
    """Payloads of the outbox rows of event `name`, oldest first."""
    rows = owner_rows(db, "SELECT payload FROM outbox WHERE name = %s ORDER BY id", (name,))
    return [row[0] for row in rows]


# --- Recurrence and day close (P0-19) ---------------------------------------------------------

TzWorkspace = Callable[..., Awaitable[None]]


@pytest.fixture
def tz_workspace(db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock) -> TzWorkspace:
    """`tz_workspace(tz, at=None)`: sets the workspace timezone through
    `auth.api.put_workspace_settings` at `at` (default: the clock's now). The timezone's
    change time is the day-close anchor, and a PUT keeping the zone leaves it, so the
    helper then fixes `workspaces.timezone_changed_at` to `at` either way."""
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    async def set_timezone(tz: str, at: datetime | None = None) -> None:
        when = at or clock.now()
        [(version,)] = owner_rows(
            db, "SELECT version FROM workspaces WHERE id = %s", (workspace.id,)
        )
        await auth.put_workspace_settings(
            workspace.ctx, auth.WorkspaceSettingsIn(timezone=tz, version=version), now=when
        )
        with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
            conn.execute(
                b"UPDATE workspaces SET timezone_changed_at = %s WHERE id = %s",
                (when, workspace.id),
            )

    return set_timezone


async def drain_workflows(timeout_s: float = 20) -> None:
    """Wait until no DBOS workflow is enqueued or pending (the tick's children ran)."""
    import asyncio  # noqa: PLC0415

    from dbos import DBOS  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        busy = await DBOS.list_workflows_async(status=["ENQUEUED", "PENDING"])
        if not busy:
            return
        if loop.time() > deadline:
            names = sorted(w.name for w in busy)
            pytest.fail(f"workflows still running after {timeout_s} s: {names}")
        await asyncio.sleep(0.05)
