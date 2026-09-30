"""Archiving a project's excerpts (P2-18, FR-5.10): the context items the project owns move
into one `archived_blobs` blob (module `integrations`, kind `context_items`) in the same
transaction as they leave `context_items`, and come back, ids and times included, on
unarchive. The steps `projects.workflows` runs through its archive hooks (registered at
the bottom; integrations.workflows imports this module in the worker).
"""

from typing import Final
from uuid import UUID

from sqlalchemy import ColumnElement, Table, delete

from tumnis.core import archive_blobs as blobs
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.integrations.models import ContextItem
from tumnis.modules.projects import api as projects

MODULE: Final = "integrations"
CONTEXT_ITEMS: Final = "context_items"
REF: Final = "project"  # one blob per project

_items: Table = ContextItem.__table__  # type: ignore[assignment]


def _ctx(workspace_id: UUID) -> WorkspaceContext:
    return WorkspaceContext(workspace_id, SYSTEM_ACTOR)


def _owned(project_id: UUID) -> list[ColumnElement[bool]]:
    return [_items.c.owner_type == "project", _items.c.owner_id == project_id]


async def archive_excerpts(workspace_id: UUID, project_id: UUID) -> None:
    async with tenant_session(_ctx(workspace_id)) as s:
        rows = await blobs.snapshot_rows(
            s, "context_items", "t.owner_type = 'project' AND t.owner_id = :p", {"p": project_id}
        )
        if not rows:
            return
        await blobs.put_blob(
            s,
            module=MODULE,
            kind=CONTEXT_ITEMS,
            project_id=project_id,
            ref=REF,
            raw=blobs.encode_rows(rows),
        )
        await s.execute(delete(_items).where(*_owned(project_id)))


async def restore_excerpts(workspace_id: UUID, project_id: UUID) -> None:
    async with tenant_session(_ctx(workspace_id)) as s:
        for _ref, raw in await blobs.blobs(
            s, module=MODULE, kind=CONTEXT_ITEMS, project_id=project_id
        ):
            await blobs.restore_rows(s, "context_items", blobs.decode_rows(raw))
        await blobs.delete_blobs(s, module=MODULE, kind=CONTEXT_ITEMS, project_id=project_id)


async def purge_archive(workspace_id: UUID, project_id: UUID) -> None:
    async with tenant_session(_ctx(workspace_id)) as s:
        await blobs.delete_blobs(s, module=MODULE, project_id=project_id)


for _name, _hook in {
    "integrations.archive_excerpts": archive_excerpts,
    "integrations.restore_excerpts": restore_excerpts,
    "integrations.purge_archive": purge_archive,
}.items():
    projects.register_archive_hook(_name, _hook)
