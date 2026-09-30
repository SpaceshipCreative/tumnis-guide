"""The folder backup manifest (P1-15, REL-1): every Tumnis-made folder is backed up; an
existing folder only when the project opted in."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.knowledge.tests.integration._folder_runner import SELF_HOSTED

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("REL-1")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
async def test_manifest_includes_tumnis_made_and_opted_in_only(
    db: DbUrls, knowledge_ws: WorkspaceHandle, clock: FixedClock, tmp_location: Path
) -> None:
    """T-P1-15-11
    `backup_sources()` lists each Tumnis-made folder and each `existing` folder with
    `backup_opt_in`, as the path rclone copies from and a destination unique to the
    project under the backup remote; an existing folder without the opt-in is left out,
    and so is a deleted project folder.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ws = knowledge_ws
    async with tenant_session(ws.ctx) as s:
        location = await knowledge.create_location(
            s,
            knowledge.LocationIn(name="disk", kind="server_path", root=str(tmp_location)),
            net=SELF_HOSTED,
        )
    ids: dict[str, UUID] = {}
    roots: dict[str, str] = {}
    for name in ("Made", "Existing", "Opted in", "Gone"):
        async with tenant_session(ws.ctx) as s:
            project = await projects.create_project(
                s, ws.ctx.actor, projects.ProjectCreate(name=name), now=clock.now()
            )
            folder = await knowledge.assign_project_folder(s, project.id)
        assert folder is not None
        ids[name], roots[name] = project.id, folder.root_path
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(
            "UPDATE project_folders SET mode = 'existing' WHERE project_id = ANY(%s)",
            ([ids["Existing"], ids["Opted in"]],),
        )
        conn.execute(
            "UPDATE project_folders SET backup_opt_in = true WHERE project_id = %s",
            (ids["Opted in"],),
        )
        conn.execute(
            "UPDATE project_folders SET deleted_at = now() WHERE project_id = %s", (ids["Gone"],)
        )

    async with tenant_session(ws.ctx) as s:
        sources = await knowledge.backup_sources(s)

    got = {(src.project_id, src.location_id, src.source, src.dest, src.mode) for src in sources}
    assert got == {
        (
            ids["Made"],
            location.id,
            f"{tmp_location}/{roots['Made']}",
            f"{ws.id}/{ids['Made']}",
            "tumnis_made",
        ),
        (
            ids["Opted in"],
            location.id,
            f"{tmp_location}/{roots['Opted in']}",
            f"{ws.id}/{ids['Opted in']}",
            "existing",
        ),
    }
