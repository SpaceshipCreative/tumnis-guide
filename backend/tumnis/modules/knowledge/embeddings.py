"""Chunk embeddings (P3-10, FR-15.3, FR-11.10, R-37): which embedding models a workspace
has, embedding a document's chunks, and switching to a new model in the background.
Re-exported by `knowledge.api`.

- Text goes to an embedder only through `decisions.embed`, which routes it (Data flow rule
  6: a local-only project's text never reaches a hosted embedder). Only `context_text`
  (the heading path and the chunk's text) is sent, never a whole file.
- A model's `embedding_models` row says its state. The first time a document is embedded
  with no row for the preferred allowed model, that model becomes `active`. A later model
  is set with `set_embedding_model` (`building`), filled by the `knowledge_reembed_all`
  workflow (`start_reembed`) once its partial HNSW index exists, then made `active` while
  the model it replaces is `retired` and its rows deleted. Search and indexing use, per
  project, the first allowed embedder whose model is `active` (`search_model`), so search
  keeps working on the old model during a build.
- Creating an index is DDL, so the owner role does it (`tumnis embeddings index <model>`,
  `create_embedding_index`); the app role never runs DDL.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Table, and_, delete, exists, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tumnis.core import tenancy
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.modules.decisions import api as decisions
from tumnis.modules.knowledge.models import Chunk, Document, Embedding, EmbeddingModel
from tumnis.modules.knowledge.rules import (
    COSINE_OPCLASS,
    EMBED_BATCH,
    EMBED_QUEUE,
    HNSW_MAX_DIMS,
    REEMBED_WORKFLOW,
    hnsw_index_name,
    valid_model_name,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from dbos import DBOSClient
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import ColumnElement

__all__ = [
    "EmbeddingModelOut",
    "ReembedRequest",
    "SearchModel",
    "create_embedding_index",
    "embed_document",
    "embedding_models",
    "reembed_batch",
    "reembed_finish",
    "reembed_index_ready",
    "reembed_workflow_id",
    "search_model",
    "set_embedding_model",
    "start_reembed",
]

_models: Table = EmbeddingModel.__table__  # type: ignore[assignment]
_embeddings: Table = Embedding.__table__  # type: ignore[assignment]
_chunks: Table = Chunk.__table__  # type: ignore[assignment]
_documents: Table = Document.__table__  # type: ignore[assignment]

HNSW_M: Final = 16  # pgvector's defaults, stated explicitly (plan)
HNSW_EF_CONSTRUCTION: Final = 64


class EmbeddingModelOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    dims: int
    provider: str
    status: str  # building | active | retired
    index_name: str | None


class ReembedRequest(BaseModel):
    """What `start_reembed` runs: fill `model`, then retire `replaces` (None: nothing)."""

    model_config = ConfigDict(frozen=True)

    model: str
    replaces: str | None


@dataclass(frozen=True)
class SearchModel:
    model: str
    dims: int


async def embedding_models(s: AsyncSession) -> list[EmbeddingModelOut]:
    """The workspace's embedding models, oldest first."""
    rows = (
        (await s.execute(select(_models).order_by(_models.c.created_at, _models.c.model)))
        .mappings()
        .all()
    )
    return [EmbeddingModelOut.model_validate(dict(row)) for row in rows]


async def _index_name(s: AsyncSession, model: str) -> str | None:
    """The model's partial HNSW index, when it exists (`pg_indexes` is readable by all)."""
    found = await s.scalar(
        text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'embeddings' AND indexname = :name"
        ),
        {"name": hnsw_index_name(model)},
    )
    return None if found is None else str(found)


async def search_model(s: AsyncSession, project_id: UUID | None) -> SearchModel | None:
    """The model a project's text is embedded and searched with: the first embedder the
    project may use whose model is `active` (None: none is)."""
    allowed = await decisions.embedders_for(project_id)
    if not allowed:
        return None
    rows = {m.model: m for m in await embedding_models(s)}
    for info in allowed:
        row = rows.get(info.model)
        if row is not None and row.status == "active":
            return SearchModel(row.model, row.dims)
    return None


async def _index_target(s: AsyncSession, project_id: UUID | None) -> SearchModel | None:
    """The model a document is embedded with at index time: the search model, else the
    first allowed embedder the workspace has no row for yet, which becomes `active`."""
    found = await search_model(s, project_id)
    if found is not None:
        return found
    known = {m.model for m in await embedding_models(s)}
    for info in await decisions.embedders_for(project_id):
        if info.model in known or not valid_model_name(info.model):
            continue
        await s.execute(
            pg_insert(_models)
            .values(
                model=info.model,
                dims=info.dims,
                provider=info.provider,
                status="active",
                index_name=await _index_name(s, info.model),
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "model"])
        )
        return await search_model(s, project_id)
    return None


def _missing(model: str) -> ColumnElement[bool]:
    """The chunk has no vector from `model` yet."""
    return ~exists().where(_embeddings.c.chunk_id == _chunks.c.id, _embeddings.c.model == model)


def _check_dims(vectors: Sequence[Sequence[float]], count: int, dims: int, model: str) -> None:
    if len(vectors) != count or any(len(v) != dims for v in vectors):
        raise ValueError(f"embedder {model} answered vectors that are not {count} x {dims}")


async def _write(
    ctx: WorkspaceContext,
    model: str,
    project_id: UUID | None,
    chunk_ids: Sequence[UUID],
    vectors: Sequence[Sequence[float]],
) -> None:
    """One row per chunk; a chunk embedded already (a replayed step) keeps its row."""
    async with tenant_session(ctx) as s:
        await s.execute(
            pg_insert(_embeddings)
            .values(
                [
                    {
                        "chunk_id": chunk_id,
                        "project_id": project_id,
                        "model": model,
                        "embedding": list(vector),
                    }
                    for chunk_id, vector in zip(chunk_ids, vectors, strict=True)
                ]
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "chunk_id", "model"])
        )


async def _embed_rows(
    ctx: WorkspaceContext,
    target: SearchModel,
    project_id: UUID | None,
    rows: Sequence[tuple[UUID, str]],
) -> int:
    """Embed `rows` (chunk id, context text) with the target model in batches of
    EMBED_BATCH, writing each batch; 0 when the slot skips them (nothing was sent)."""
    written = 0
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start : start + EMBED_BATCH]
        result = await decisions.embed([t for _, t in batch], project_id, model=target.model)
        if result.skipped:
            return written
        _check_dims(result.vectors, len(batch), target.dims, target.model)
        await _write(ctx, target.model, project_id, [c for c, _ in batch], result.vectors)
        written += len(batch)
    return written


async def embed_document(
    ctx: WorkspaceContext, document_id: UUID, version_id: UUID | None = None
) -> int:
    """Embed the chunks of the document's version (default: its current one) that have no
    vector from the project's index model yet; how many were written. Nothing happens
    (0) when no embedder may take the project's text. Adapter errors propagate: callers
    (extraction step 8, the event subscribers) log them and carry on."""
    with tenancy.use_workspace(ctx):
        # A project's embedders are a subset of the workspace knowledge base's.
        if not await decisions.embedders_for(None):
            return 0
        async with tenant_session(ctx) as s:
            doc = (
                await s.execute(
                    select(_documents.c.project_id, _documents.c.current_version_id).where(
                        _documents.c.id == document_id
                    )
                )
            ).first()
            if doc is None:
                return 0
            vid = version_id or doc.current_version_id
            if vid is None:
                return 0
            target = await _index_target(s, doc.project_id)
            if target is None:
                return 0
            rows = [
                (row.id, row.context_text)
                for row in (
                    await s.execute(
                        select(_chunks.c.id, _chunks.c.context_text)
                        .where(
                            _chunks.c.document_version_id == vid,
                            _chunks.c.deleted_at.is_(None),
                            _missing(target.model),
                        )
                        .order_by(_chunks.c.ordinal, _chunks.c.id)
                    )
                ).all()
            ]
        return await _embed_rows(ctx, target, doc.project_id, rows)


async def set_embedding_model(
    s: AsyncSession, model: str, dims: int, provider: str
) -> ReembedRequest:
    """Make `model` (of `dims` dimensions) the workspace's next model: `building` until
    `reembed_all` has embedded every chunk with it. It replaces the active model the slot
    prefers (else the oldest active one). A model already active replaces nothing."""
    if not valid_model_name(model) or not 1 <= dims <= HNSW_MAX_DIMS:
        raise ValueError(f"not an embedding model Tumnis accepts: {model!r} ({dims} dims)")
    rows = {m.model: m for m in await embedding_models(s)}
    if (row := rows.get(model)) is not None and row.status == "active":
        return ReembedRequest(model=model, replaces=None)
    active = [m for m in rows.values() if m.status == "active" and m.model != model]
    preferred = [info.model for info in await decisions.embedders_for(None)]
    active.sort(key=lambda m: preferred.index(m.model) if m.model in preferred else len(preferred))
    index_name = await _index_name(s, model)
    stmt = pg_insert(_models).values(
        model=model, dims=dims, provider=provider, status="building", index_name=index_name
    )
    await s.execute(
        stmt.on_conflict_do_update(
            index_elements=["workspace_id", "model"],
            set_={
                "dims": stmt.excluded.dims,
                "provider": stmt.excluded.provider,
                "status": "building",
                "index_name": stmt.excluded.index_name,
                "updated_at": func.now(),
            },
        )
    )
    return ReembedRequest(model=model, replaces=active[0].model if active else None)


def reembed_workflow_id(workspace_id: UUID, model: str) -> str:
    return f"reembed:{workspace_id}:{model}"


# The worker's in-process enqueue (`knowledge.workflows`), registered on its import.
_starters: list[Callable[[str, str, str, str | None], Awaitable[None]]] = []


def register_reembed_starter(fn: Callable[[str, str, str, str | None], Awaitable[None]]) -> None:
    _starters[:] = [fn]


async def start_reembed(
    ctx: WorkspaceContext, request: ReembedRequest, *, client: DBOSClient | None = None
) -> str:
    """Queue `knowledge_reembed_all` on the `embed` queue (workflow id
    `reembed:<workspace>:<model>`, so a repeat returns the same workflow): through
    `client` from the api process, else in this process's DBOS."""
    workflow_id = reembed_workflow_id(ctx.workspace_id, request.model)
    args = (str(ctx.workspace_id), request.model, request.replaces)
    if client is not None:
        options: Any = {
            "queue_name": EMBED_QUEUE,
            "workflow_name": REEMBED_WORKFLOW,
            "workflow_id": workflow_id,
        }
        await client.enqueue_async(options, *args)
        return workflow_id
    if not _starters:
        importlib.import_module("tumnis.modules.knowledge.workflows")  # registers it
    await _starters[0](workflow_id, *args)
    return workflow_id


def create_embedding_index(owner_url: str, model: str, dims: int) -> str:
    """Create `model`'s partial HNSW index over `embedding::vector(dims)` as the owner role
    (`tumnis embeddings index`), then record its name on every workspace's row of the model
    (the owner bypasses row-level security). Idempotent. Returns the index name."""
    import psycopg  # noqa: PLC0415  # the CLI's one-off owner connection
    from psycopg import sql  # noqa: PLC0415
    from sqlalchemy.engine import make_url  # noqa: PLC0415

    if not valid_model_name(model) or not 1 <= dims <= HNSW_MAX_DIMS:
        raise ValueError(f"not an embedding model Tumnis accepts: {model!r} ({dims} dims)")
    name = hnsw_index_name(model)
    dsn = make_url(owner_url).set(drivername="postgresql").render_as_string(hide_password=False)
    ddl = sql.SQL(
        "CREATE INDEX IF NOT EXISTS {name} ON embeddings"
        " USING hnsw ((embedding::vector({dims})) {opclass})"
        " WITH (m = {m}, ef_construction = {ef}) WHERE model = {model}"
    ).format(
        name=sql.Identifier(name),
        dims=sql.Literal(dims),
        opclass=sql.SQL(COSINE_OPCLASS),
        m=sql.Literal(HNSW_M),
        ef=sql.Literal(HNSW_EF_CONSTRUCTION),
        model=sql.Literal(model),
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(ddl)
        conn.execute(
            "UPDATE embedding_models SET index_name = %s, updated_at = now() WHERE model = %s",
            (name, model),
        )
    return name


# --- reembed_all's steps (knowledge.workflows) ----------------------------------------------


async def _blocked_projects(s: AsyncSession, model: str) -> tuple[list[UUID], bool]:
    """The projects whose text may not go to `model` (local-only with a hosted model), and
    whether the workspace knowledge base may not either."""
    present: Sequence[UUID | None] = (
        (await s.execute(select(_documents.c.project_id).distinct())).scalars().all()
    )
    blocked: list[UUID] = []
    kb_blocked = False
    for project_id in present:
        allowed = {info.model for info in await decisions.embedders_for(project_id)}
        if model in allowed:
            continue
        if project_id is None:
            kb_blocked = True
        else:
            blocked.append(project_id)
    return blocked, kb_blocked


async def _pending_stmt(s: AsyncSession, model: str) -> Any:
    """The current versions' chunks that still need a vector from `model` and may go to it."""
    blocked, kb_blocked = await _blocked_projects(s, model)
    stmt = (
        select(_chunks.c.id, _chunks.c.context_text, _documents.c.project_id)
        .join(
            _documents,
            and_(
                _documents.c.id == _chunks.c.document_id,
                _documents.c.current_version_id == _chunks.c.document_version_id,
            ),
        )
        .where(_chunks.c.deleted_at.is_(None), _missing(model))
    )
    if blocked:
        stmt = stmt.where(
            _documents.c.project_id.is_(None) | _documents.c.project_id.not_in(blocked)
        )
    if kb_blocked:
        stmt = stmt.where(_documents.c.project_id.is_not(None))
    return stmt


async def _row(s: AsyncSession, model: str) -> EmbeddingModelOut | None:
    rows = {m.model: m for m in await embedding_models(s)}
    return rows.get(model)


async def reembed_index_ready(ctx: WorkspaceContext, model: str) -> bool:
    """Whether `model`'s partial HNSW index exists (its row names it)."""
    async with tenant_session(ctx) as s:
        row = await _row(s, model)
        if row is None:
            raise LookupError(f"no embedding model {model!r} in this workspace")
        if row.index_name is None and (found := await _index_name(s, model)) is not None:
            await s.execute(
                update(_models).where(_models.c.model == model).values(index_name=found)
            )
            return True
        return row.index_name is not None


async def reembed_batch(ctx: WorkspaceContext, model: str) -> int:
    """Embed the next EMBED_BATCH chunks lacking a vector from `model` (one embedder call
    per project among them); how many there were."""
    with tenancy.use_workspace(ctx):
        async with tenant_session(ctx) as s:
            row = await _row(s, model)
            if row is None:
                raise LookupError(f"no embedding model {model!r} in this workspace")
            stmt = await _pending_stmt(s, model)
            pending = (await s.execute(stmt.order_by(_chunks.c.id).limit(EMBED_BATCH))).all()
        groups: dict[UUID | None, list[tuple[UUID, str]]] = {}
        for chunk_id, context_text, project_id in pending:
            groups.setdefault(project_id, []).append((chunk_id, context_text))
        target = SearchModel(row.model, row.dims)
        for project_id, rows in groups.items():
            if await _embed_rows(ctx, target, project_id, rows) < len(rows):
                raise RuntimeError(f"embedder {model} is not available for this text")
        return len(pending)


async def reembed_finish(ctx: WorkspaceContext, model: str, replaces: str | None) -> int:
    """`model` active, `replaces` retired and its rows deleted, in one transaction, when no
    chunk still lacks a vector from `model`; else nothing changes. `replaces` stays active
    while some project's text may not go to `model`. How many still lack one (the workflow
    embeds those and tries again)."""
    with tenancy.use_workspace(ctx):
        async with tenant_session(ctx) as s:
            stmt = await _pending_stmt(s, model)
            remaining = int(await s.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
            if remaining:
                return remaining
            await s.execute(
                update(_models)
                .where(_models.c.model == model)
                .values(status="active", updated_at=func.now())
            )
            # A project `model` may not take (local-only, hosted model) keeps searching
            # with `replaces`, so it stays active with its vectors.
            blocked, kb_blocked = await _blocked_projects(s, model)
            if replaces is not None and replaces != model and not blocked and not kb_blocked:
                await s.execute(
                    update(_models)
                    .where(_models.c.model == replaces)
                    .values(status="retired", updated_at=func.now())
                )
                await s.execute(delete(_embeddings).where(_embeddings.c.model == replaces))
        return 0
