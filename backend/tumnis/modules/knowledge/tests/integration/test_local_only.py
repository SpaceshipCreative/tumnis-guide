"""Local-only routing of the Embeddings slot (P3-10, FR-11.10, Data flow rule 6): a project
whose decisions stay local never has its text sent to a hosted embedder, even when the
hosted one is the slot's primary; with no local embedder its chunks are not embedded at
all, and hybrid search falls back to the full-text results without an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

BODY = "## Hosting\n\nThe staging server runs in the client's own data centre.\n\n" * 3


async def _local_project(ctx: WorkspaceContext, clock: FixedClock) -> tuple[UUID, UUID]:
    """A local-only project ("Beta app") with one text entry: (project id, document id)."""
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
        doc = await knowledge.create_text_entry(s, project.id, "Hosting notes", BODY)
    return project.id, doc.id


async def _search(ctx: WorkspaceContext, q: str, project_id: UUID, mode: str) -> list[Any]:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        return await knowledge.search_knowledge(s, q, project_id=project_id, limit=10, mode=mode)  # type: ignore[arg-type]


@pytest.mark.req("FR-11.10")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
async def test_local_only_project_never_sends_text_to_hosted_embedder(
    knowledge_ws: WorkspaceHandle,
    db: DbUrls,
    clock: FixedClock,
    use_embedders: Callable[..., None],
) -> None:
    """T-P3-10-09
    With a hosted embedder as the slot's primary and a local one beside it, a local-only
    project's chunks and its search queries go to the local fake only: the hosted fake's
    call log stays empty, and the chunks' vectors are the local model's.
    """
    from tumnis.modules.decisions.adapters.embeddings.fake import (  # noqa: PLC0415
        FakeEmbeddings,
    )
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.tests.integration._upload import rows  # noqa: PLC0415

    hosted = FakeEmbeddings(model="hosted-embed", dims=4, hosted=True)
    local = FakeEmbeddings(model="local-embed", dims=4)
    use_embedders(hosted, local, primary="hosted-embed")
    project_id, doc_id = await _local_project(knowledge_ws.ctx, clock)

    await knowledge.embed_document(knowledge_ws.ctx, doc_id)  # type: ignore[attr-defined]
    hits = await _search(knowledge_ws.ctx, "where does staging run", project_id, "hybrid")

    assert hosted.calls == []
    sent = [text for batch in local.calls for text in batch]
    assert any("staging server" in text for text in sent)
    assert "where does staging run" in sent
    assert hits
    models = rows(db, "SELECT DISTINCT model FROM embeddings")
    assert models == [{"model": "local-embed"}]


@pytest.mark.req("FR-15.3", "FR-11.10")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
async def test_no_local_embedder_falls_back_to_fulltext(
    knowledge_ws: WorkspaceHandle,
    db: DbUrls,
    clock: FixedClock,
    use_embedders: Callable[..., None],
) -> None:
    """T-P3-10-10
    With only a hosted embedder, a local-only project's chunks are skipped (no rows, no
    call), and hybrid search answers exactly what full-text search answers, without an
    error.
    """
    from tumnis.modules.decisions.adapters.embeddings.fake import FakeEmbeddings  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.tests.integration._upload import scalar  # noqa: PLC0415

    hosted = FakeEmbeddings(model="hosted-embed", dims=4, hosted=True)
    use_embedders(hosted)
    project_id, doc_id = await _local_project(knowledge_ws.ctx, clock)

    written = await knowledge.embed_document(knowledge_ws.ctx, doc_id)  # type: ignore[attr-defined]
    hybrid = await _search(knowledge_ws.ctx, "staging server", project_id, "hybrid")
    fulltext = await _search(knowledge_ws.ctx, "staging server", project_id, "fts")

    assert written == 0
    assert hosted.calls == []
    assert scalar(db, "SELECT count(*) FROM embeddings") == 0
    assert fulltext
    assert [h.chunk_id for h in hybrid] == [h.chunk_id for h in fulltext]
