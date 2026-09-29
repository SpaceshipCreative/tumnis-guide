"""Helpers for the search spec tests (P0-20). No assertions live here: spec-guard locks the
test bodies, and this module is where later work packages plug in their shapes.

- `insert_doc(ctx, ...)`: one `search_index` row written directly (TDD step 2 inserts rows
  before the subscribers exist); returns its entity id.
- `load_corpus()`: `backend/fixtures/search/corpus.yaml` (T-P0-20-08).
- `corpus_rows(ctx, corpus, now)`: the corpus's rows inserted in ctx's workspace; returns
  entity id -> document key.
- `drain(db)`: the relay run until the outbox is empty, then every search delivery of the
  rows it sent awaited (the other modules' subscribers run too, unawaited).
- `index_outbox(db)`: every outbox row applied to the index through `search.api`, one
  transaction per workspace, without DBOS (the seed and load sets write thousands).
- `trash_task(ctx, task_id, at)`: the smallest seam for a task trash until its write
  lands (P0-24: "delete means trash"): sets `deleted_at` and emits `task.updated` with
  the deleted doc, as that write will. Built from the event registry, so search imports
  nothing from tasks.
- `index_row(db, entity_id)`: the row's title, deleted_at and source_updated_at, read as
  the owner (None when there is no row).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import yaml
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    from datetime import datetime

    from dbos import WorkflowHandleAsync

    from tests._pg import DbUrls
    from tumnis.core.tenancy import WorkspaceContext

CORPUS = Path(__file__).resolve().parents[5] / "fixtures" / "search" / "corpus.yaml"

_INSERT = """
INSERT INTO search_index (workspace_id, entity_type, entity_id, project_id, title, body,
                          source_updated_at, deleted_at)
VALUES (:ws, :entity_type, :entity_id, :project_id, :title, :body, :at, :deleted_at)
"""


async def insert_doc(  # noqa: PLR0917
    ctx: WorkspaceContext,
    title: str,
    at: datetime,
    body: str = "",
    entity_type: str = "task",
    project_id: uuid.UUID | None = None,
    entity_id: uuid.UUID | None = None,
    deleted: bool = False,
) -> uuid.UUID:
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    entity_id = entity_id or uuid.uuid4()
    async with tenant_session(ctx) as s:
        await s.execute(
            text(_INSERT),
            {
                "ws": ctx.workspace_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "project_id": project_id,
                "title": title,
                "body": body,
                "at": at,
                "deleted_at": at if deleted else None,
            },
        )
    return entity_id


def load_corpus() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CORPUS.read_text())
    return data


def corpus_project(corpus: dict[str, Any], name: str | None) -> uuid.UUID | None:
    return None if name is None else uuid.UUID(corpus["projects"][name])


async def corpus_rows(
    ctx: WorkspaceContext, corpus: dict[str, Any], now: datetime
) -> dict[uuid.UUID, str]:
    keys: dict[uuid.UUID, str] = {}
    for doc in corpus["documents"]:
        project_id = corpus_project(corpus, doc.get("project"))
        entity_id = await insert_doc(
            ctx,
            doc["title"],
            now - timedelta(days=doc["age_days"]),
            body=doc.get("body", ""),
            entity_type=doc["type"],
            project_id=project_id,
            entity_id=project_id if doc["type"] == "project" else None,
        )
        keys[entity_id] = doc["key"]
    return keys


def _owner_rows(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(query.encode(), params).fetchall()


async def drain(db: DbUrls) -> None:
    from dbos import DBOS  # noqa: PLC0415

    import tumnis.modules.search.events  # noqa: F401, PLC0415  # registers the subscribers
    from tumnis.core.events import delivery_id, relay_once, subscribers_for  # noqa: PLC0415

    pending = _owner_rows(db, "SELECT event_id, name FROM outbox WHERE sent_at IS NULL")
    while await relay_once():
        pass
    for row in pending:
        for sub in subscribers_for(row["name"]):
            if sub.module == "search":
                handle: WorkflowHandleAsync[str] = await DBOS.retrieve_workflow_async(
                    delivery_id(row["event_id"], sub.name)
                )
                await handle.get_result()


async def index_outbox(db: DbUrls) -> int:
    """Applies every outbox row; returns how many changed the index."""
    from tumnis.core.events import EventEnvelope  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.search import api  # noqa: PLC0415

    by_workspace: dict[uuid.UUID, list[EventEnvelope]] = defaultdict(list)
    for row in _owner_rows(db, "SELECT * FROM outbox ORDER BY id"):
        envelope = EventEnvelope.from_outbox_row(row)
        by_workspace[envelope.workspace_id].append(envelope)
    applied = 0
    for workspace_id, envelopes in by_workspace.items():
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
            for envelope in envelopes:
                applied += await api.index_event(s, envelope)
    return applied


async def trash_task(ctx: WorkspaceContext, task_id: uuid.UUID, at: datetime) -> None:
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core.events import registry  # noqa: PLC0415
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        row = (
            (
                await s.execute(
                    text(
                        "UPDATE tasks SET deleted_at = :at, version = version + 1"
                        " WHERE id = :id RETURNING title, project_id"
                    ),
                    {"at": at, "id": task_id},
                )
            )
            .mappings()
            .one()
        )
        model = registry.model("task.updated", 1)
        payload = model.model_validate(
            {
                "task_id": task_id,
                "changed_fields": ["deleted"],
                "doc": {
                    "title": row["title"],
                    "deleted": True,
                    "project_id": row["project_id"],
                    "updated_at": at,
                },
            }
        )
        await emit(s, payload, occurred_at=at)


def index_row(db: DbUrls, entity_id: uuid.UUID) -> dict[str, Any] | None:
    rows = _owner_rows(
        db,
        "SELECT title, project_id, deleted_at, source_updated_at FROM search_index"
        " WHERE entity_id = %s",
        (entity_id,),
    )
    return rows[0] if rows else None
