"""Until the `project.created` subscriber has run, a project has no brief (P0-17, R-13):
`get_brief` raises NotFound, in the caller's session or the workspace context."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-2.3")
@pytest.mark.wp("P0-17")
async def test_brief_is_not_found_before_the_subscriber_runs(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.versioning import NotFound  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    async with tenant_session(workspace.ctx) as s:
        project = await projects.create_project(
            s, workspace.ctx.actor, projects.ProjectCreate(name="No relay"), now=clock.now()
        )
        with pytest.raises(NotFound):
            await knowledge.get_brief(project.id, session=s)
    with pytest.raises(NotFound):
        await knowledge.get_brief(project.id)
