"""Hybrid knowledge search (P3-10, FR-15.3): `search_knowledge(q, ..., mode="hybrid")` runs
full-text search and a vector search over the Embeddings slot's vectors (pgvector, one
partial HNSW index per model) and fuses the two ranked lists by reciprocal rank fusion.
Every hit still cites its document, heading path and page; on the committed eval set the
fused recall@5 is no worse than full-text alone; `passages_for` uses it; and vector hits
stay inside the workspace (row-level security) and the reader's project scope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _search(ctx: Any, q: str, project_id: UUID | None, mode: str, limit: int = 5) -> Any:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        return await knowledge.search_knowledge(s, q, project_id=project_id, limit=limit, mode=mode)  # type: ignore[arg-type]


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
async def test_every_hit_keeps_document_heading_and_page(
    knowledge_ws: WorkspaceHandle, clock: FixedClock, use_embedders: Callable[..., None]
) -> None:
    """T-P3-10-04
    With the eval corpus loaded and embedded on its recorded vectors, every hybrid hit of
    every query names its document and title, its heading path, and its page (null only
    for documents without pages); hybrid finds hits full-text search does not (the vector
    list is merged in) and keeps full-text's own hits.
    """
    from tumnis.modules.knowledge.tests._eval import (  # noqa: PLC0415
        embed_corpus,
        load_eval_corpus,
        queries,
        recorded_embeddings,
    )

    use_embedders(recorded_embeddings())
    loaded = await load_eval_corpus(knowledge_ws.ctx, clock)
    await embed_corpus(knowledge_ws.ctx, loaded)
    acme = loaded.projects["acme"]

    vector_only = 0
    for query in queries():
        hybrid = await _search(knowledge_ws.ctx, query["text"], acme, "hybrid", limit=10)
        fulltext = await _search(knowledge_ws.ctx, query["text"], acme, "fts", limit=10)
        assert hybrid, f"no hybrid hits for {query['query_id']}"
        for hit in hybrid:
            assert loaded.chunks[hit.chunk_id] == hit.document_id
            assert hit.document_title
            assert hit.heading_path
            assert hit.text
            assert (hit.page is not None) == loaded.paged[hit.document_id]
        hybrid_ids = {hit.chunk_id for hit in hybrid}
        fts_ids = [hit.chunk_id for hit in fulltext]
        assert set(fts_ids[:3]) <= hybrid_ids  # full-text's best hits are kept
        vector_only += len(hybrid_ids - set(fts_ids))
    assert vector_only > 0


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
async def test_recall_not_worse_than_fulltext_on_eval_set(
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    use_embedders: Callable[..., None],
    record_property: Callable[[str, object], None],
) -> None:
    """T-P3-10-05
    On the committed eval set (at least 40 queries, a third each keyword, paraphrase and
    mixed, with vectors recorded from the real model), recall@5 of hybrid search is no
    worse than recall@5 of full-text search; both numbers go into the test report.
    """
    from tumnis.modules.knowledge.rules import (  # noqa: PLC0415
        recall_at_k,
    )
    from tumnis.modules.knowledge.tests._eval import (  # noqa: PLC0415
        embed_corpus,
        load_eval_corpus,
        queries,
        recorded_embeddings,
    )

    qs = queries()
    assert len(qs) >= 40
    kinds = [q["kind"] for q in qs]
    assert {kinds.count(k) for k in ("keyword", "paraphrase", "mixed")} == {len(qs) // 3}
    use_embedders(recorded_embeddings())
    loaded = await load_eval_corpus(knowledge_ws.ctx, clock)
    await embed_corpus(knowledge_ws.ctx, loaded)

    results: dict[str, dict[str, list[UUID]]] = {"fts": {}, "hybrid": {}}
    for q in qs:
        project_id = loaded.projects[q["project"]]
        for mode, found in results.items():
            hits = await _search(knowledge_ws.ctx, q["text"], project_id, mode, limit=5)
            found[q["query_id"]] = [hit.chunk_id for hit in hits]
    expected = {q["query_id"]: {UUID(c) for c in q["expected_chunk_ids"]} for q in qs}
    fulltext = recall_at_k(results["fts"], expected, k=5)
    hybrid = recall_at_k(results["hybrid"], expected, k=5)
    record_property("recall_at_5_fulltext", round(fulltext, 4))
    record_property("recall_at_5_hybrid", round(hybrid, 4))

    assert hybrid >= fulltext, f"hybrid recall@5 {hybrid:.3f} < full-text {fulltext:.3f}"


@pytest.mark.req("PERF-1")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
async def test_hnsw_index_used(
    load_fixture: SeedResult, knowledge_ws: WorkspaceHandle, db: DbUrls
) -> None:
    """T-P3-10-06
    With the load fixture and 5,000 synthetic chunks carrying random unit vectors of the
    default model (1,024 dimensions), analysed, the plan of the vector query for a project
    is an index scan on the default model's partial HNSW index.
    """
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import search  # noqa: PLC0415
    from tumnis.modules.knowledge.rules import (  # noqa: PLC0415
        CANDIDATES,
        DEFAULT_EMBEDDING_DIMS,
        DEFAULT_EMBEDDING_MODEL,
        hnsw_index_name,
    )
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ctx = knowledge_ws.ctx
    async with tenant_session(ctx) as s:
        project = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name="Acme site")
        )
        doc = await knowledge.create_text_entry(s, project.id, "Load", "Synthetic chunks.")
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO chunks (workspace_id, document_id, document_version_id, ordinal, text,"
            " context_text, created_by)"
            " SELECT d.workspace_id, d.id, d.current_version_id, 1000 + n, 'chunk ' || n,"
            " 'chunk ' || n, 'system' FROM documents d, generate_series(1, 5000) n"
            " WHERE d.id = %s",
            (doc.id,),
        )
        conn.execute(
            "INSERT INTO embeddings (workspace_id, chunk_id, project_id, model, embedding,"
            " created_by)"
            " SELECT c.workspace_id, c.id, %s, %s,"
            " l2_normalize((SELECT array_agg(random() - 0.5) FROM generate_series(1, %s)"
            "  WHERE c.id IS NOT NULL)::vector), 'system'"
            " FROM chunks c WHERE c.document_id = %s",
            (project.id, DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_DIMS, doc.id),
        )
        conn.execute("ANALYZE embeddings")
    probe = [1.0] + [0.0] * (DEFAULT_EMBEDDING_DIMS - 1)
    sql, params = search.vector_query(
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_EMBEDDING_DIMS,
        probe,
        project_id=project.id,
        project_ids=None,
        candidates=CANDIDATES,
    )

    async with tenant_session(ctx) as s:
        await search.tune_session(s)
        plan = "\n".join((await s.execute(text(f"EXPLAIN {sql}"), params)).scalars())

    assert f"Index Scan using {hnsw_index_name(DEFAULT_EMBEDDING_MODEL)}" in plan, plan


@pytest.mark.req("FR-15.4", "FR-15.3")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
async def test_passages_for_task_uses_hybrid_and_brief_first(
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    use_embedders: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P3-10-12
    `passages_for(task)` searches in hybrid mode, still puts the project brief first and
    keeps the passages within PASSAGE_CAP_CHARS.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.embeddings.fake import (  # noqa: PLC0415
        FakeEmbeddings,
    )
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.rules import PASSAGE_CAP_CHARS  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    use_embedders(FakeEmbeddings())
    ctx = knowledge_ws.ctx
    brief = "Acme's site rebuild. Quotes use the rate card's hourly rates."
    async with tenant_session(ctx) as s:
        project = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name="Acme site"), now=clock.now()
        )
        brief_id = await knowledge.put_text_document(
            s, project.id, title="Acme site brief", body_md=brief, role="brief"
        )
        made = [
            await knowledge.create_text_entry(
                s, project.id, f"Rates {n}", f"# Rates\n\nQuote the redesign at rate {n}. " * 40
            )
            for n in range(6)
        ]
        task = await tasks.create_task(
            s,
            ctx.actor,
            tasks.TaskCreate(project_id=project.id, title="Quote Acme for the redesign"),
            now=clock.now(),
        )
    for doc in made:
        await knowledge.embed_document(ctx, doc.id)
    modes: list[str] = []
    real = knowledge.search_knowledge

    async def spy(*args: Any, **kw: Any) -> Any:
        modes.append(kw.get("mode", "fts"))
        return await real(*args, **kw)

    monkeypatch.setattr(knowledge, "search_knowledge", spy)

    async with tenant_session(ctx) as s:
        out = await knowledge.passages_for(s, task.id)

    assert modes == ["hybrid"]
    assert out[0].document_id == brief_id
    assert out[0].chunk_id is None
    assert len(out) > 1
    assert sum(len(p.text) for p in out) <= PASSAGE_CAP_CHARS


@pytest.mark.req("FR-15.3", "FR-15.1")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
async def test_results_workspace_and_project_isolated(
    knowledge_ws: WorkspaceHandle,
    db: DbUrls,
    clock: FixedClock,
    use_embedders: Callable[..., None],
) -> None:
    """T-P3-10-13
    Every chunk of three workspaces' projects is embedded, so the vector list alone would
    find all of them; a hybrid search in project P returns P's items and the workspace
    knowledge base only, never project Q's or another workspace's (row-level security),
    and the other workspace's search never returns this one's.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.embeddings.fake import FakeEmbeddings  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    use_embedders(FakeEmbeddings())
    ctx = knowledge_ws.ctx
    other = WorkspaceContext(make_workspace(db, "Other"), SYSTEM_ACTOR)
    async with tenant_session(ctx) as s:
        p = await projects.create_project(s, ctx.actor, projects.ProjectCreate(name="P"))
        q = await projects.create_project(s, ctx.actor, projects.ProjectCreate(name="Q"))
        mine = await knowledge.create_text_entry(s, p.id, "Ours", "Invoice terms for P.")
        shared = await knowledge.create_text_entry(s, None, "Shared", "Studio holiday rules.")
        theirs = await knowledge.create_text_entry(s, q.id, "Q's", "Invoice terms for Q.")
    async with tenant_session(other) as s:
        foreign = await knowledge.create_text_entry(s, None, "Foreign", "Invoice terms abroad.")
    for doc in (mine, shared, theirs):
        await knowledge.embed_document(ctx, doc.id)
    await knowledge.embed_document(other, foreign.id)

    hits = await _search(ctx, "invoice", p.id, "hybrid", limit=50)
    found = {hit.document_id for hit in hits}
    assert found == {mine.id, shared.id}  # the vector list reached the shared entry too
    their_hits = await _search(other, "invoice", None, "hybrid", limit=50)
    assert {hit.document_id for hit in their_hits} == {foreign.id}
