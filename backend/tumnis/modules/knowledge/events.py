"""knowledge event payload models and subscribers.

`knowledge.create_brief` (P0-17, FR-2.3): on `project.created`, the project's brief
(`brief_md` from the payload) becomes its pinned, trusted text document. Idempotent: the
brief index lets one row exist per project, so a redelivered event writes nothing. The
payload is read as a dict, so knowledge needs nothing from projects.

`knowledge.assign_project_folder` (P1-14, FR-15.7; P1-15, FR-15.12): on `project.created`,
the project gets its folder on the workspace default location, made there with its layout
when the location answers (`api.ensure_project_folder`); idempotent through the folder's
unique key (nothing happens while the workspace has no default location). Never rename a
subscriber: its name is part of every delivery's workflow ID.

The `document.added` and `document.changed` payloads (P1-16) live in `payloads.py` and are
re-exported here.

`knowledge.embed_added_document` / `knowledge.embed_changed_document` (P3-10, FR-15.3): the
new version's chunks get their vectors from the Embeddings slot. Text entries are indexed
in the api process, which never calls an embedder, so their vectors come from here (the
worker's relay); a file's were written in extraction step 8 already, and a chunk with a
vector is not sent again. Nothing happens while the slot has no embedder; a failed
embedding is logged, never retried into the dead letters (search falls back to full text,
and `start_reembed` fills any gap later).
"""

import logging
from typing import Any
from uuid import UUID

from tumnis.core.events import EventEnvelope, subscribe
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.knowledge import api, sync
from tumnis.modules.knowledge.payloads import DocumentAddedV1, DocumentChangedV1

__all__ = ["DocumentAddedV1", "DocumentChangedV1"]

log = logging.getLogger(__name__)


@subscribe("project.created", name="knowledge.create_brief")
async def create_brief(envelope: EventEnvelope) -> None:
    payload: dict[str, Any] = envelope.payload
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    async with tenant_session(ctx) as s:
        await api.create_brief(
            s,
            project_id=payload["project_id"],
            title=f"{payload.get('name', 'Project')} brief",
            body_md=payload.get("brief_md", ""),
        )


@subscribe("project.created", name="knowledge.assign_project_folder")
async def assign_project_folder(envelope: EventEnvelope) -> None:
    payload: dict[str, Any] = envelope.payload
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    async with tenant_session(ctx) as s:
        await api.ensure_project_folder(s, UUID(str(payload["project_id"])), net=sync.net())


async def _embed(envelope: EventEnvelope) -> None:
    payload: dict[str, Any] = envelope.payload
    ctx = WorkspaceContext(envelope.workspace_id, SYSTEM_ACTOR)
    try:
        await api.embed_document(
            ctx, UUID(str(payload["document_id"])), UUID(str(payload["version_id"]))
        )
    except Exception:  # the embedder is an improvement, never a gate
        log.warning("embedding document %s failed; full text only", payload["document_id"])


@subscribe("document.added", name="knowledge.embed_added_document")
async def embed_added_document(envelope: EventEnvelope) -> None:
    await _embed(envelope)


@subscribe("document.changed", name="knowledge.embed_changed_document")
async def embed_changed_document(envelope: EventEnvelope) -> None:
    await _embed(envelope)
