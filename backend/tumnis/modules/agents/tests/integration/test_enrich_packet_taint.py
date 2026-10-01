"""An enrich packet is tainted when outside text reaches it (P1-17, SAF-1, P2-08): a
tainted passage (an upload, a synced outside file) taints the request, the packet and,
through `enrich_task`'s apply step, the task it writes."""

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

RATE_NOTES = "# Rate notes\n\n## Senior designer\n\nThe senior designer quote rate is 160.\n"


@pytest.mark.req("SAF-1", "FR-15.4")
@pytest.mark.wp("P1-17")
async def test_tainted_passage_taints_the_enrich_packet(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """The same task's enrich packet is clean while its passage comes from trusted text,
    and tainted (request and packet) once that document is tainted."""
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import packet_builder  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    ctx = workspace.ctx
    async with tenant_session(ctx) as s:
        acme = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name="Acme site"), now=clock.now()
        )
        notes = await knowledge.create_text_entry(s, acme.id, "Rate notes", RATE_NOTES)
        task = await tasks.create_task(
            s,
            ctx.actor,
            tasks.TaskCreate(project_id=acme.id, title="Quote Acme for a senior designer"),
            now=clock.now(),
        )

    async with tenant_session(ctx) as s:
        clean = await packet_builder.enrich_packet(s, task.id)
    assert clean.body["passages"], "the rate notes were not found"
    assert clean.tainted is False

    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute("UPDATE documents SET tainted = true WHERE id = %s", (notes.id,))

    async with tenant_session(ctx) as s:
        _request, tainted = await packet_builder.enrichment_request_tainted(
            s, task.id, missing=["first_action"]
        )
        packet = await packet_builder.enrich_packet(s, task.id)
    assert tainted is True
    assert packet.tainted is True
    assert packet.body["passages"] == clean.body["passages"]
