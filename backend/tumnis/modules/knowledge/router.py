"""knowledge FastAPI router under /v1/knowledge; thin calls into api.py."""

# --- P0-24: the project page's Brief rail -----------------------------------------------
# The brief read sits under /v1/projects/{id}/brief and the text-entry edit under
# /v1/knowledge/documents/{id}, so this router takes no prefix. Keep this block separate.

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, StringConstraints

from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.versioning import Version
from tumnis.modules.knowledge import api

router = v1_router("knowledge", tags=["knowledge"])


class TextDocumentPatch(BaseModel):
    body_md: Annotated[str, StringConstraints(max_length=100_000)]
    version: Version


@router.get("/projects/{project_id}/brief")
@route_policy(
    RoutePolicy(
        auth="session_or_key",
        scopes=frozenset({"context:read"}),
        project_param="path:project_id",
    )
)
async def get_brief(project_id: UUID, session: SessionDep) -> api.DocumentDTO:
    """The project's pinned brief (a text entry); 404 until the project's subscriber ran."""
    return await api.get_brief(project_id, session=session)


@router.patch("/knowledge/documents/{document_id}")
@route_policy(
    RoutePolicy(
        auth="session_or_key",
        scopes=frozenset({"knowledge:write"}),
        idempotent=True,
        project_param="lookup:knowledge",
    )
)
async def update_document(
    document_id: UUID, body: TextDocumentPatch, session: SessionDep
) -> api.DocumentDTO:
    """Replace a text entry's Markdown body; 409 `stale_version` with the current entry."""
    return await api.update_text_document(
        session, document_id, body_md=body.body_md, version=body.version
    )
