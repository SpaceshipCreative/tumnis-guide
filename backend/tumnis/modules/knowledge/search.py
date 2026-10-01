"""Hybrid knowledge search (P3-10, FR-15.3): the vector half of
`knowledge.api.search_knowledge(..., mode="hybrid")`, and the fusion.

Full-text search's top CANDIDATES and the vector search's top CANDIDATES (pgvector, the
search model's partial HNSW index, cosine distance) are fused by reciprocal rank fusion
(`rules.rrf_merge`): only ranks count, since a text rank and a cosine distance are not
comparable. `knowledge.api` (which imports this file, so nothing here imports it) reads
vector-only hits back through the full-text path's own filters (the reader's scope,
current versions of live, ready documents), so every hit cites its
document, heading path and page, and row-level security plus that scope keep it inside
the workspace and the reader's projects.

When no query vector is available (the Embeddings slot is off in this process, no model
is active for the project, the project's text may not go to any embedder, or the embedder
fails or takes longer than `decisions.query_timeout_s()`), hybrid search answers exactly
what full-text search answers. The api process does not configure the slot (it never
calls out: AGENTS.md), so there hybrid is full text until Scott decides otherwise.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import text

from tumnis.modules.decisions import api as decisions
from tumnis.modules.knowledge import embeddings
from tumnis.modules.knowledge.models import vector_literal
from tumnis.modules.knowledge.rules import (
    CANDIDATES,
    COSINE_OPERATOR,
    HNSW_MAX_DIMS,
    FusedHit,
    Ranked,
    rrf_merge,
    valid_model_name,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["EF_SEARCH", "fuse", "tune_session", "vector_query"]

log = logging.getLogger(__name__)

EF_SEARCH: Final = 100  # plan default (pgvector's default is 40)


async def tune_session(s: AsyncSession) -> None:
    """HNSW search settings for this transaction only (`SET LOCAL`, safe behind PgBouncer's
    transaction pooling): a wider candidate list, and iterative scans so a project filter
    that drops rows still leaves enough results (pgvector 0.8 `relaxed_order`).
    `set_config(..., true)` is `SET LOCAL` with the value bound."""
    await s.execute(
        text("SELECT set_config('hnsw.ef_search', :value, true)"), {"value": str(EF_SEARCH)}
    )
    await s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))


def vector_query(  # the query's vector and the reader's scope
    model: str,
    dims: int,
    vector: Sequence[float],
    *,
    project_id: UUID | None,
    project_ids: frozenset[UUID] | None,
    candidates: int,
) -> tuple[str, dict[str, Any]]:
    """The vector query as SQL and its parameters: the nearest `candidates` chunks to
    `vector` among `model`'s rows in the reader's scope (a project's and the workspace
    knowledge base's). The model and the dimension are written as literals, matching the
    model's partial index (`WHERE model = '<model>'` over `embedding::vector(<dims>)`)
    exactly, so the planner can use it; only the vector and the scope are bound."""
    if not valid_model_name(model):
        raise ValueError(f"not an embedding model name Tumnis accepts: {model!r}")
    if not 1 <= dims <= HNSW_MAX_DIMS:
        raise ValueError(f"dimension out of range: {dims}")
    literal = model.replace("'", "''")  # a valid name has no quote; doubled all the same
    distance = f"(e.embedding::vector({int(dims)})) {COSINE_OPERATOR} CAST(:q AS vector)"
    where = [f"e.model = '{literal}'"]
    params: dict[str, Any] = {"q": vector_literal(list(vector)), "candidates": int(candidates)}
    if project_id is not None:
        where.append("(e.project_id = :project_id OR e.project_id IS NULL)")
        params["project_id"] = project_id
    elif project_ids is not None:
        where.append("(e.project_id = ANY(:project_ids) OR e.project_id IS NULL)")
        params["project_ids"] = list(project_ids)
    sql = (
        f"SELECT e.chunk_id, {distance} AS distance FROM embeddings e"  # noqa: S608  # literals validated above
        f" WHERE {' AND '.join(where)} ORDER BY {distance} LIMIT :candidates"
    )
    return sql, params


async def _query_vector(
    s: AsyncSession, q: str, project_id: UUID | None
) -> tuple[embeddings.SearchModel, list[float]] | None:
    """The search model's vector for `q`, or None (full text only)."""
    target = await embeddings.search_model(s, project_id)
    if target is None:
        return None
    try:
        async with asyncio.timeout(decisions.query_timeout_s()):
            result = await decisions.embed([q], project_id, model=target.model)
    except Exception:  # any embedder failure: the search still answers, with full text
        log.warning("query embedding with %s failed; full-text results only", target.model)
        return None
    if result.skipped or len(result.vectors) != 1 or len(result.vectors[0]) != target.dims:
        return None
    return target, result.vectors[0]


async def fuse(  # the full-text ranking, and the reader's scope
    s: AsyncSession,
    q: str,
    fulltext: Sequence[UUID],
    *,
    project_id: UUID | None,
    project_ids: frozenset[UUID] | None,
) -> list[FusedHit] | None:
    """The full-text ranking (chunk ids, best first) fused with the vector search's top
    CANDIDATES by RRF, every chunk of either list, best first; None when no query vector
    exists (the caller answers with full text alone). Vector hits are only candidates:
    `knowledge.api` reads them back through the full-text path's filters."""
    found = await _query_vector(s, q, project_id)
    if found is None:
        return None
    target, vector = found
    await tune_session(s)
    sql, params = vector_query(
        target.model,
        target.dims,
        vector,
        project_id=project_id,
        project_ids=project_ids,
        candidates=CANDIDATES,
    )
    nearest = [row.chunk_id for row in (await s.execute(text(sql), params)).all()]
    return rrf_merge(
        [Ranked(chunk_id, n) for n, chunk_id in enumerate(fulltext, start=1)],
        [Ranked(chunk_id, n) for n, chunk_id in enumerate(nearest, start=1)],
        limit=len(fulltext) + len(nearest),
    )
