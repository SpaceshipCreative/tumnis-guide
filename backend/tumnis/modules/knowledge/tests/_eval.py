"""The search eval set (P3-10, FR-15.3): `backend/fixtures/search_eval/`, loaded straight
into a workspace, and the embeddings fake that answers with its recorded vectors.

`load_eval_corpus(ctx, clock)` makes the corpus's projects ("acme" is "Acme site", "beta"
is "Beta app"), one text document per corpus document and exactly the corpus's chunks in
its current version (ids, heading paths, pages and text as recorded; `context_text` is the
heading path and the text joined by newlines, as `knowledge.api._index_text` builds it,
which is the text the recorded vectors were computed from). No assertions live here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext

EVAL_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "search_eval"
EVAL_MODEL = "BAAI/bge-m3"  # the model the vectors were recorded with (README)
EVAL_DIMS = 1024
PROJECT_NAMES = {"acme": "Acme site", "beta": "Beta app"}


def _jsonl(name: str) -> list[dict[str, Any]]:
    lines = (EVAL_DIR / name).read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def corpus() -> list[dict[str, Any]]:
    return _jsonl("corpus.jsonl")


def queries() -> list[dict[str, Any]]:
    return _jsonl("queries.jsonl")


def context_text(heading_path: list[str], text: str) -> str:
    return "\n".join([*heading_path, text])


@dataclass
class EvalCorpus:
    projects: dict[str, UUID] = field(default_factory=dict)  # corpus key -> project id
    documents: dict[UUID, UUID] = field(default_factory=dict)  # corpus id -> document id
    paged: dict[UUID, bool] = field(default_factory=dict)  # document id -> has pages
    chunks: dict[UUID, UUID] = field(default_factory=dict)  # chunk id -> document id


async def load_eval_corpus(ctx: WorkspaceContext, clock: FixedClock) -> EvalCorpus:
    from sqlalchemy import Table, delete, insert  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.models import Chunk, Document  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    chunks: Table = Chunk.__table__  # type: ignore[assignment]
    loaded = EvalCorpus()
    async with tenant_session(ctx) as s:
        for key, name in PROJECT_NAMES.items():
            project = await projects.create_project(
                s, ctx.actor, projects.ProjectCreate(name=name), now=clock.now()
            )
            loaded.projects[key] = project.id
        for doc in corpus():
            project_id = None if doc["project"] is None else loaded.projects[doc["project"]]
            body = "\n\n".join(chunk["text"] for chunk in doc["chunks"])
            made = await knowledge.create_text_entry(s, project_id, doc["title"], body)
            version_id = await s.scalar(
                Document.__table__.select()
                .with_only_columns(Document.__table__.c.current_version_id)
                .where(Document.__table__.c.id == made.id)
            )
            await s.execute(delete(chunks).where(chunks.c.document_version_id == version_id))
            await s.execute(
                insert(chunks),
                [
                    {
                        "id": UUID(chunk["chunk_id"]),
                        "document_id": made.id,
                        "document_version_id": version_id,
                        "ordinal": chunk["ordinal"],
                        "text": chunk["text"],
                        "context_text": context_text(chunk["heading_path"], chunk["text"]),
                        "heading_path": chunk["heading_path"],
                        "page_from": chunk["page_from"],
                        "page_to": chunk["page_to"],
                    }
                    for chunk in doc["chunks"]
                ],
            )
            loaded.documents[UUID(doc["document_id"])] = made.id
            loaded.paged[made.id] = bool(doc["paged"])
            for chunk in doc["chunks"]:
                loaded.chunks[UUID(chunk["chunk_id"])] = made.id
    return loaded


def recorded_embeddings() -> Any:
    """The fake on the recorded vectors: a text it has no vector for raises."""
    from tumnis.modules.decisions.adapters.embeddings.fake import (  # type: ignore[import-untyped]  # noqa: PLC0415
        FakeEmbeddings,
    )

    return FakeEmbeddings(model=EVAL_MODEL, dims=EVAL_DIMS, recorded=EVAL_DIR / "vectors")


async def embed_corpus(ctx: WorkspaceContext, loaded: EvalCorpus) -> None:
    """Every loaded document's chunks embedded through `knowledge.api.embed_document`."""
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    for document_id in loaded.documents.values():
        await knowledge.embed_document(ctx, document_id)  # type: ignore[attr-defined]
