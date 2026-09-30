"""Project folder names on one location stay distinct when projects are placed at once
(P1-15, FR-15.12): naming is serialized per location, so two projects of one name never
share a folder."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tumnis.modules.knowledge.tests.integration._folder_runner import SELF_HOSTED

if TYPE_CHECKING:
    from pathlib import Path

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

NAMES = ["Acme:", "Acme?", "Acme*", "Acme|"]  # each sanitizes to "Acme-"


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
async def test_concurrent_projects_of_one_name_get_distinct_folders(
    db: DbUrls, knowledge_ws: WorkspaceHandle, clock: FixedClock, tmp_location: Path
) -> None:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ws = knowledge_ws
    async with tenant_session(ws.ctx) as s:
        await knowledge.create_location(
            s,
            knowledge.LocationIn(
                name="disk", kind="server_path", root=str(tmp_location), is_default=True
            ),
            net=SELF_HOSTED,
        )
    ids: list[UUID] = []
    for name in NAMES:  # project names are unique; their folder names are not
        async with tenant_session(ws.ctx) as s:
            made = await projects.create_project(
                s, ws.ctx.actor, projects.ProjectCreate(name=name), now=clock.now()
            )
            ids.append(made.id)

    async def place(project_id: UUID) -> str:
        async with tenant_session(ws.ctx) as s:
            folder = await knowledge.assign_project_folder(s, project_id)
        assert folder is not None
        return folder.root_path

    names = await asyncio.gather(*(place(pid) for pid in ids))
    assert sorted(names) == ["Acme-", "Acme- 2", "Acme- 3", "Acme- 4"]
