"""Issue #56: `POST /v1/test/reset` failed with a 500 in its seed step, now and then.

The seed's briefs go through `put_text_document`, whose upsert names the partial brief
index (`ux_documents_ws_brief`, `WHERE role = 'brief'`). The predicate went out as a bound
parameter (`WHERE role = $1`). psycopg prepares a statement on a connection after five runs,
and Postgres then tries a generic plan, where `role = $1` cannot prove the index predicate:
"there is no unique or exclusion constraint matching the ON CONFLICT specification". The
brief subscriber's upsert names the same index. `plan_cache_mode = force_generic_plan`
gives both the plan a reused pooled connection ends up with.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-2.3")
@pytest.mark.wp("P0-17")
async def test_issue_56_brief_upserts_work_under_a_generic_plan(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    async with tenant_session(workspace.ctx) as s:
        project = await projects.create_project(
            s, workspace.ctx.actor, projects.ProjectCreate(name="Acme"), now=clock.now()
        )
        await s.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
        # The subscriber writes the brief, then the seed replaces its title and body.
        assert await knowledge.create_brief(s, project.id, "Acme brief", "# Goal")
        await knowledge.put_text_document(
            s, project.id, title="Acme seed brief", body_md="# Seeded", role="brief"
        )
        assert not await knowledge.create_brief(s, project.id, "Acme brief", "# Again")

    with psycopg.connect(db.libpq(OWNER)) as conn:
        rows = conn.execute(
            "SELECT title, body_md FROM documents WHERE role = 'brief' AND project_id = %s",
            (project.id,),
        ).fetchall()
    assert rows == [("Acme seed brief", "# Seeded")]
