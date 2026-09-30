"""Fixtures for the coolify integration tests (P2-14).

- `app_db`: tumnis.core.db pointed at the per-test database (no pooling).
- `coolify`: a fresh `FakeCoolifyStatus` (every recorded application) wired into the poll
  workflow for the test.
- `link_project(name, *app_uuids, archived=False)`: a project in the `workspace` fixture's
  workspace linking those Coolify applications, made through `projects.api`.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.coolify.adapters.fake import FakeCoolifyStatus
    from tumnis.modules.projects.api import ProjectOut

LinkProject = Callable[..., Awaitable["ProjectOut"]]


@pytest.fixture
async def app_db(db: DbUrls, master_key_file: MasterKeyFile) -> AsyncIterator[DbUrls]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield db
    finally:
        await core_db.dispose()


@pytest.fixture
def coolify() -> Iterator[FakeCoolifyStatus]:
    from tumnis.modules.coolify import workflows  # noqa: PLC0415
    from tumnis.modules.coolify.adapters.fake import FakeCoolifyStatus  # noqa: PLC0415

    fake = FakeCoolifyStatus()
    previous = workflows.use(lambda workspace_id, settings: fake)
    try:
        yield fake
    finally:
        workflows.use(previous)


@pytest.fixture
def link_project(app_db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock) -> LinkProject:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.projects import api  # noqa: PLC0415

    counter = itertools.count(1)

    async def make(name: str, *app_uuids: str, archived: bool = False) -> ProjectOut:
        links = [api.ProjectLinkIn(kind="coolify_app", value=uuid) for uuid in app_uuids]
        links.append(api.ProjectLinkIn(kind="domain", value=f"site{next(counter)}.example.com"))
        data = api.ProjectCreate(name=name, links=links)
        async with tenant_session(workspace.ctx) as s:
            project = await api.create_project(s, SYSTEM_ACTOR, data, now=clock.now())
            if archived:
                project = await api.archive_project(
                    s, SYSTEM_ACTOR, project.id, project.version, now=clock.now()
                )
        return project

    return make
