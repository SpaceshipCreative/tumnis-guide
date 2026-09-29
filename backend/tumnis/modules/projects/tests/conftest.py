"""Fixtures for the projects tests (P0-17); the shared ones (app, clock, db, workspace,
session_client, query_counter) come from backend/tests/fixtures.

- `FakeStatsSource`: a `ProjectStatsSource` answering from a dict keyed by project id,
  recording each call's ids (the N+1 check); `fake_stats` registers one for a test and
  puts the default (zeros) back afterwards.
- `make_project(**overrides)`: a project in the `workspace` fixture's workspace, made
  through `projects.api.create_project` (P0-18 onward builds on it).
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.projects.api import ProjectOut, ProjectStats


@dataclass
class FakeStatsSource:
    """Stats per project id (zeros for an id it does not know); `calls` holds the ids of
    every call, in order."""

    by_project: dict[UUID, ProjectStats] = field(default_factory=dict)
    calls: list[list[UUID]] = field(default_factory=list)

    async def stats(
        self, s: AsyncSession, project_ids: Sequence[UUID], today: date
    ) -> Mapping[UUID, ProjectStats]:
        from tumnis.modules.projects.api import ProjectStats  # noqa: PLC0415
        from tumnis.modules.projects.rules import HealthFacts  # noqa: PLC0415

        self.calls.append(list(project_ids))
        zero = ProjectStats(
            health_facts=HealthFacts(waiting_on_human=0, overdue=0),
            open_count=0,
            next_open_due=None,
        )
        return {pid: self.by_project.get(pid, zero) for pid in project_ids}


@pytest.fixture
def fake_stats() -> Iterator[FakeStatsSource]:
    """A FakeStatsSource registered for the test; the default source comes back after."""
    from tumnis.modules.projects import api  # noqa: PLC0415

    fake = FakeStatsSource()
    previous = api.stats_source()
    api.register_stats_source(fake)
    try:
        yield fake
    finally:
        api.register_stats_source(previous)


MakeProject = Callable[..., Awaitable["ProjectOut"]]


@pytest.fixture
def make_project(db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock) -> MakeProject:
    """`await make_project(**overrides)`: a project (name "Project <n>" unless given) in
    the `workspace` fixture's workspace, made by its user through the api."""
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR, ActorRef  # noqa: PLC0415
    from tumnis.modules.projects import api  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    actor = ActorRef(f"user:{workspace.user_id}") if workspace.user_id else SYSTEM_ACTOR
    ctx = WorkspaceContext(workspace.id, actor)
    counter = itertools.count(1)

    async def make(**overrides: Any) -> ProjectOut:
        overrides.setdefault("name", f"Project {next(counter)}")
        data = api.ProjectCreate(**overrides)
        async with tenant_session(ctx) as s:
            return await api.create_project(s, actor, data, now=clock.now())

    return make
