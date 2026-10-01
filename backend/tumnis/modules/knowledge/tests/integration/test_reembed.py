"""Re-embedding on a model change (P3-10, FR-11.10, REL-3): a new embedding model is
`building` while the `reembed_all` workflow on the `embed` queue embeds every chunk in
batches of 64; search keeps using the old model until the new one is `active`; then the
old model's rows are deleted and it is `retired`. A worker killed mid-batch is replaced by
one that finishes the job without embedding any chunk twice.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.knowledge.tests.integration._upload import scalar

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from uuid import UUID

    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.tenancy import WorkspaceContext

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

EMBED_PROBE = "tumnis.modules.knowledge.tests.integration._embed_probe"
KILLED_EXIT = 137


def _sections(n: int, word: str) -> str:
    """A Markdown body of `n` sections: `n` chunks."""
    return "\n\n".join(f"## Part {i}\n\nThe {word} clause number {i}." for i in range(n))


async def _corpus(ctx: WorkspaceContext, chunks: int) -> UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        doc = await knowledge.create_text_entry(s, None, "Contract", _sections(chunks, "invoice"))
    await knowledge.embed_document(ctx, doc.id)
    return doc.id


async def _models(ctx: WorkspaceContext) -> dict[str, str]:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        return {m.model: m.status for m in await knowledge.embedding_models(s)}


def _rows(db: DbUrls, model: str) -> int:
    return int(scalar(db, "SELECT count(*) FROM embeddings WHERE model = %s", model))


@pytest.mark.req("FR-11.10")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
async def test_model_change_reembeds_in_background(
    knowledge_ws: WorkspaceHandle,
    dbos: type[DBOS],
    db: DbUrls,
    use_embedders: Callable[..., None],
) -> None:
    """T-P3-10-07
    Given model A active with all 70 chunks embedded, when the user sets model B (6
    dimensions instead of 4) and B's index exists: B is `building`, `reembed_all` embeds
    the chunks in batches of 64 and 6, hybrid search keeps embedding queries with A while B
    builds, then B is `active`, A is `retired` and A's rows are gone.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.embeddings.fake import (  # noqa: PLC0415
        FakeEmbeddings,
    )
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.rules import (  # noqa: PLC0415
        EMBED_BATCH,
    )

    ctx = knowledge_ws.ctx
    model_a = FakeEmbeddings(model="model-a", dims=4)
    model_b = FakeEmbeddings(model="model-b", dims=6)
    use_embedders(model_a)
    await _corpus(ctx, 70)
    assert await _models(ctx) == {"model-a": "active"}
    assert _rows(db, "model-a") == 70

    index = knowledge.create_embedding_index(db.owner, "model-b", 6)
    use_embedders(model_b, model_a)
    model_b.hold = asyncio.Event()  # B answers only when the test lets it
    async with tenant_session(ctx) as s:
        request = await knowledge.set_embedding_model(s, "model-b", 6, "fake")
    assert request.replaces == "model-a"
    workflow_id = await knowledge.start_reembed(ctx, request)
    assert await _models(ctx) == {"model-a": "active", "model-b": "building"}

    model_a.calls.clear()
    hits = await _hybrid(ctx, "invoice clause")
    assert hits
    assert model_a.calls == [["invoice clause"]]  # the query went to A, the active model

    model_b.hold.set()
    handle: Any = await dbos.retrieve_workflow_async(workflow_id)
    await asyncio.wait_for(handle.get_result(), 60)

    assert [len(batch) for batch in model_b.calls] == [EMBED_BATCH, 70 - EMBED_BATCH]
    assert EMBED_BATCH == 64
    assert await _models(ctx) == {"model-a": "retired", "model-b": "active"}
    assert _rows(db, "model-a") == 0
    assert _rows(db, "model-b") == 70
    assert index.startswith("embeddings_hnsw_")
    model_b.calls.clear()
    assert await _hybrid(ctx, "invoice clause")
    assert model_b.calls == [["invoice clause"]]


async def _hybrid(ctx: WorkspaceContext, q: str) -> list[Any]:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        return await knowledge.search_knowledge(s, q, project_id=None, limit=5, mode="hybrid")


@pytest.mark.req("REL-3", "FR-11.10")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
async def test_reembed_resumes_after_kill(  # noqa: PLR0917
    knowledge_ws: WorkspaceHandle,
    worker_killer: WorkerKillerFactory,
    db: DbUrls,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_embedders: Callable[..., None],
) -> None:
    """T-P3-10-08
    A worker killed after the first batch of `reembed_all` (kill point
    `knowledge.reembed.batch_1`) is replaced by one that recovers the workflow: it ends in
    SUCCESS, each of the 150 chunks was sent to model B exactly once across both workers,
    B holds 150 rows and is active, and A's rows are gone.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.embeddings.fake import FakeEmbeddings  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.rules import (  # noqa: PLC0415
        EMBED_QUEUE,
        REEMBED_WORKFLOW,
    )

    ctx = knowledge_ws.ctx
    use_embedders(FakeEmbeddings(model="model-a", dims=4))
    await _corpus(ctx, 150)
    knowledge.create_embedding_index(db.owner, "model-b", 6)
    async with tenant_session(ctx) as s:
        request = await knowledge.set_embedding_model(s, "model-b", 6, "fake")
    log = tmp_path / "embedded.log"
    monkeypatch.setenv("KNOWLEDGE_EMBED_LOG", str(log))
    killer = worker_killer("knowledge.reembed.batch_1", events=0, imports=(EMBED_PROBE,))
    workflow_id = f"reembed:{ctx.workspace_id}:model-b"

    code = await killer.enqueue_until_killed(
        queue_name=EMBED_QUEUE,
        workflow_name=REEMBED_WORKFLOW,
        workflow_id=workflow_id,
        args=(str(ctx.workspace_id), request.model, request.replaces),
    )

    assert code == KILLED_EXIT
    assert len(log.read_text().splitlines()) == 64
    assert await killer.restart_until_done(workflow_id) == "SUCCESS"
    sent = log.read_text().splitlines()
    assert len(sent) == 150
    assert len(set(sent)) == 150
    assert _rows(db, "model-b") == 150
    assert _rows(db, "model-a") == 0
    assert await _models(ctx) == {"model-a": "retired", "model-b": "active"}
