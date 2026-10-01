"""Switching to a hosted embedding model keeps the old local one for local-only projects
(P3-10 review follow-up, FR-11.10, Data flow rule 6): the hosted model never receives a
local-only project's text, so the re-embed leaves that project's chunks without a vector
from it. The old local model then stays `active`, with its vectors, so the project's hybrid
search still has a model to embed queries with.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests.integration._upload import scalar

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

BODY = "## Hosting\n\nThe staging server runs in the client's own data centre.\n"


async def _documents(ctx: WorkspaceContext, clock: FixedClock) -> tuple[UUID, list[UUID]]:
    """A workspace knowledge base entry and a local-only project's entry: (project, docs)."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        project = await projects.create_project(
            s, SYSTEM_ACTOR, projects.ProjectCreate(name="Beta app"), now=clock.now()
        )
    async with tenant_session(ctx) as s:
        patch = projects.ProjectPatch(local_decisions_only=True, version=project.version)
        await projects.update_project(
            s, SYSTEM_ACTOR, project.id, patch, project.version, now=clock.now()
        )
        local = await knowledge.create_text_entry(s, project.id, "Hosting notes", BODY)
        shared = await knowledge.create_text_entry(s, None, "Office notes", BODY)
    return project.id, [local.id, shared.id]


def _rows(db: DbUrls, model: str) -> int:
    return int(scalar(db, "SELECT count(*) FROM embeddings WHERE model = %s", model))


@pytest.mark.req("FR-11.10")
@pytest.mark.wp("P3-10")
async def test_hosted_switch_keeps_local_model_for_local_only_projects(
    knowledge_ws: WorkspaceHandle,
    db: DbUrls,
    clock: FixedClock,
    use_embedders: Callable[..., None],
) -> None:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.embeddings.fake import (  # noqa: PLC0415
        FakeEmbeddings,
    )
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import embeddings  # noqa: PLC0415

    ctx = knowledge_ws.ctx
    local = FakeEmbeddings(model="local-embed", dims=4)
    hosted = FakeEmbeddings(model="hosted-embed", dims=4, hosted=True)
    use_embedders(local)
    project_id, docs = await _documents(ctx, clock)
    for doc in docs:
        await knowledge.embed_document(ctx, doc)
    local_rows = _rows(db, "local-embed")
    assert local_rows > 0

    knowledge.create_embedding_index(db.owner, "hosted-embed", 4)
    use_embedders(hosted, local, primary="hosted-embed")
    async with tenant_session(ctx) as s:
        request = await knowledge.set_embedding_model(s, "hosted-embed", 4, "fake")
    assert request.replaces == "local-embed"
    while await embeddings.reembed_batch(ctx, "hosted-embed"):
        pass
    assert await embeddings.reembed_finish(ctx, "hosted-embed", request.replaces) == 0

    async with tenant_session(ctx) as s:
        models = {m.model: m.status for m in await knowledge.embedding_models(s)}
    assert models == {"local-embed": "active", "hosted-embed": "active"}
    assert _rows(db, "local-embed") == local_rows
    # Only the workspace knowledge base's chunks went to the hosted model.
    assert sum(len(batch) for batch in hosted.calls) == local_rows // 2

    local.calls.clear()
    async with tenant_session(ctx) as s:
        hits = await knowledge.search_knowledge(
            s, "where does staging run", project_id=project_id, limit=5, mode="hybrid"
        )
    assert hits
    assert local.calls == [["where does staging run"]]
