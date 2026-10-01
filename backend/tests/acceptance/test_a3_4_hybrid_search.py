"""A3.4 · Hybrid search with citations and the recall bar (phase 3 acceptance; turns green
with P3-10).

Preconditions: `db` with the `search_eval` corpus loaded (documents, chunks with heading
paths and pages); the embeddings fake answering with the recorded vectors from
`backend/fixtures/search_eval/vectors/` (looked up by SHA-256 of the exact text); the
full-text index built (the generated `tsv` column).

Phase 3's acceptance suite (P3-00) is folded into P3-01's spec PR, which waits on live
connector accounts; this file lands with P3-10, whose done-when it is.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.knowledge.tests._eval import EvalCorpus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
def eval_world(
    db: DbUrls, workspace: WorkspaceHandle, master_key_file: MasterKeyFile
) -> Iterator[WorkspaceHandle]:
    """The workspace, with the core database on the test database; the Embeddings slot is
    reset after the test."""
    from tumnis.core import db as core_db  # noqa: PLC0415

    del master_key_file  # requested for its order only
    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield workspace
    finally:
        try:
            from tumnis.modules.decisions import api as decisions  # noqa: PLC0415

            decisions.use_embedders(None)
        except (ImportError, AttributeError):  # before P3-10's slot exists
            pass


async def _loaded(ws: WorkspaceHandle, clock: FixedClock) -> EvalCorpus:
    from tumnis.modules.decisions import api as decisions  # noqa: PLC0415
    from tumnis.modules.knowledge.tests._eval import (  # noqa: PLC0415
        embed_corpus,
        load_eval_corpus,
        recorded_embeddings,
    )

    fake = recorded_embeddings()
    decisions.use_embedders(decisions.Embedders((fake,), primary=fake.model))
    loaded = await load_eval_corpus(ws.ctx, clock)
    await embed_corpus(ws.ctx, loaded)
    return loaded


async def _search(ws: WorkspaceHandle, q: str, project_id: UUID, mode: str) -> list[Any]:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with tenant_session(ws.ctx) as s:
        return await knowledge.search_knowledge(s, q, project_id=project_id, limit=5, mode=mode)


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
async def test_hybrid_recall_at_5_not_worse_than_fulltext(
    eval_world: WorkspaceHandle,
    clock: FixedClock,
    record_property: Callable[[str, object], None],
) -> None:
    """A3.4 (recall)
    1. For each query in queries.jsonl, search with mode "fts" and with mode "hybrid"
       (limit 5, the query's project).
    2. Compute recall@5 of both with `knowledge.rules.recall_at_k`.
    3. Hybrid is no worse than full text (the plan's bar); both numbers are recorded in
       the test report.
    """
    from tumnis.modules.knowledge.rules import recall_at_k  # noqa: PLC0415
    from tumnis.modules.knowledge.tests._eval import queries  # noqa: PLC0415

    loaded = await _loaded(eval_world, clock)
    fts: dict[str, list[UUID]] = {}
    hybrid: dict[str, list[UUID]] = {}
    for q in queries():
        project_id = loaded.projects[q["project"]]
        fts[q["query_id"]] = [
            h.chunk_id for h in await _search(eval_world, q["text"], project_id, "fts")
        ]
        hybrid[q["query_id"]] = [
            h.chunk_id for h in await _search(eval_world, q["text"], project_id, "hybrid")
        ]
    expected = {q["query_id"]: {UUID(c) for c in q["expected_chunk_ids"]} for q in queries()}
    fulltext_recall = recall_at_k(fts, expected, k=5)
    hybrid_recall = recall_at_k(hybrid, expected, k=5)
    record_property("recall_at_5_fulltext", round(fulltext_recall, 4))
    record_property("recall_at_5_hybrid", round(hybrid_recall, 4))

    assert hybrid_recall >= fulltext_recall


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
async def test_every_hit_cites_document_and_page(
    eval_world: WorkspaceHandle, clock: FixedClock
) -> None:
    """A3.4 (citations)
    4. Every hybrid hit carries its document id, document title, heading path and page
       (null only for documents without pages, such as Markdown notes, which the corpus
       marks).
    """
    from tumnis.modules.knowledge.tests._eval import queries  # noqa: PLC0415

    loaded = await _loaded(eval_world, clock)
    for q in queries():
        hits = await _search(eval_world, q["text"], loaded.projects[q["project"]], "hybrid")
        assert hits, q["query_id"]
        for hit in hits:
            assert hit.document_id == loaded.chunks[hit.chunk_id]
            assert hit.document_title
            assert hit.heading_path
            assert (hit.page is not None) == loaded.paged[hit.document_id]
