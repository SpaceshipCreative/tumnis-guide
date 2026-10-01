"""The phase 1 packet builder (P1-17, R-24, FR-15.4, FR-2.3): the enrichment request's
brief and passages come from `knowledge.api.passages_for`; the planner's projects carry a
600-character brief excerpt each; `build_packet(kind="enrich")` and the preview route
`GET /v1/tasks/{id}/packet?kind=enrich` answer the same body."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

BRIEF = "Acme's marketing site rebuild. Quotes use the hourly rates in the rate notes."
RATE_NOTES = (
    "# Rate notes\n\n## Senior designer\n\nThe senior designer quote rate is 160 an hour.\n"
)
LONG_BRIEF = " ".join(f"word{i}" for i in range(300))  # well over 600 characters


@pytest.mark.req("FR-15.4", "FR-2.3")
@pytest.mark.wp("P1-17")
@pytest.mark.xfail(strict=True, reason="spec:P1-17")
async def test_enrichment_and_planning_requests_carry_brief_and_passages(
    db: DbUrls,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    session_client: SessionClient,
) -> None:
    """T-P1-17-15
    Given a project with a brief and a text entry about senior designer rates, and a task
    asking for a senior designer quote: the enrichment request's `brief` is the brief
    passage `passages_for` returns first and its `passages` are the rest, in order, with
    chunk, document, title, heading path and page; `build_packet(kind="enrich")` carries
    that body, its prompt text holds the passage, and the preview route answers the same
    body without dispatching anything. For the planner, every project carries the first
    600 characters of its brief (`plan_projects`).
    """
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import packet_builder  # noqa: PLC0415
    from tumnis.modules.agents.rules import RunKind  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    ctx = workspace.ctx
    async with tenant_session(ctx) as s:
        acme = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name="Acme site"), now=clock.now()
        )
        beta = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name="Beta app"), now=clock.now()
        )
        await knowledge.put_text_document(
            s, acme.id, title="Acme site brief", body_md=BRIEF, role="brief"
        )
        await knowledge.put_text_document(
            s, beta.id, title="Beta app brief", body_md=LONG_BRIEF, role="brief"
        )
        await knowledge.create_text_entry(
            s,
            acme.id,
            "Rate notes",
            RATE_NOTES,
        )
        task = await tasks.create_task(
            s,
            ctx.actor,
            tasks.TaskCreate(project_id=acme.id, title="Quote Acme for a senior designer"),
            now=clock.now(),
        )

    async with tenant_session(ctx) as s:
        chosen = await knowledge.passages_for(s, task.id)
        request = await packet_builder.enrichment_request(s, task.id, missing=["first_action"])
        planned = await packet_builder.plan_projects(s, [acme.id, beta.id], now=clock.now())

    assert chosen[0].chunk_id is None
    assert len(chosen) > 1, "the rate notes were not found"
    assert request.brief == chosen[0].text == BRIEF
    assert [
        (p.chunk_id, p.document_id, p.title, p.heading_path, p.page, p.text)
        for p in request.passages
    ] == [(p.chunk_id, p.document_id, p.title, p.heading_path, p.page, p.text) for p in chosen[1:]]
    assert request.passages[0].heading_path[-1] == "Senior designer"

    excerpts = {p.id: p.brief_excerpt for p in planned}
    assert excerpts == {acme.id: BRIEF, beta.id: LONG_BRIEF[:600]}
    assert len(excerpts[beta.id]) == 600

    packet = await packet_builder.build_packet(RunKind.ENRICH, task_id=task.id, ctx=ctx)
    assert packet.kind == RunKind.ENRICH
    assert packet.body["brief"] == BRIEF
    assert packet.body["passages"] == request.model_dump(mode="json")["passages"]
    assert "160 an hour" in packet.prompt_text

    preview = await session_client.get(f"/v1/tasks/{task.id}/packet", params={"kind": "enrich"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["kind"] == "enrich"
    assert preview.json()["body"] == packet.body
    with psycopg.connect(db.libpq(OWNER)) as conn:
        dispatched = conn.execute("SELECT count(*) FROM runs WHERE task_id = %s", (task.id,))
        assert dispatched.fetchone() == (0,)
