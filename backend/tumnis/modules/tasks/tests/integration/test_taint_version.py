"""A taint raise is a system change, not an edit (P2-08, SAF-1): it stores `tainted` on the
task without bumping `version`, so linking outside content never turns a person's edit in
flight into a stale-version conflict (and T-P0-18-11's subtask keeps its version). Any
other change to the row still bumps it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.tasks.tests.conftest import owner_rows

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.tasks.tests.conftest import Actors, MakeProject, MakeTask

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

_ROW = "SELECT tainted, version FROM tasks WHERE id = %s"


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-08")
async def test_taint_raise_keeps_the_task_version(  # noqa: PLR0917
    make_project: MakeProject,
    make_task: MakeTask,
    workspace: WorkspaceHandle,
    actors: Actors,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """Attaching a tainted item to a task (integrations' owner hook) or linking one
    (`link_context_item`) taints it and leaves its version as it was; a later edit of the
    row still bumps the version."""
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    ctx = WorkspaceContext(workspace.id, actors.human)
    project = await make_project(name="Taint version project")

    attached = await make_task(project_id=project.id)
    await integrations.link_context(
        ctx,
        owner_type="task",
        owner_id=attached.id,
        target_type="url",
        target_url="https://example.com/attached-thread",
        added_by=actors.human,
    )
    assert owner_rows(db, _ROW, (attached.id,)) == [(True, attached.version)]

    linked = await make_task(project_id=project.id)
    item = await integrations.link_context(
        ctx,
        owner_type="project",
        owner_id=project.id,
        target_type="url",
        target_url="https://example.com/linked-thread",
        added_by=actors.human,
    )
    async with tenant_session(ctx) as s:
        await tasks.link_context_item(s, actors.human, linked.id, item.id, now=clock.now())
    assert owner_rows(db, _ROW, (linked.id,)) == [(True, linked.version)]

    edited = owner_rows(
        db, "UPDATE tasks SET title = 'Edited title' WHERE id = %s RETURNING version", (linked.id,)
    )
    assert edited == [(linked.version + 1,)]
