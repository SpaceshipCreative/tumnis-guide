"""knowledge MCP tools; thin calls into api.py (P2-17, FR-15.4, R-28, R-36).

| Tool | Scope | REST twin |
| --- | --- | --- |
| `search_knowledge` | context:read | `GET /v1/knowledge/search` |
| `get_document` | context:read | `GET /v1/knowledge/documents/{document_id}` |
| `add_document` | knowledge:write | `POST /v1/knowledge/documents/text` |

A project-limited caller (a task token, a project key) searches and reads its projects'
documents and the workspace knowledge base, which every project sees (FR-15.1); another
project's document is 404 `not_found` (R-28). `add_document` by a person (the web app's
text entry, P1-17) writes a trusted note under `notes/`; by any other caller an untrusted,
agent-written document under `agent-outputs/` (FR-15.5), tainted when the caller is.

This module also registers the project brief reader that `get_project_context` (projects,
P2-01) answers with: projects cannot import knowledge, which already imports projects.
"""

from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import agent_surface as surface
from tumnis.core.errors import ProblemError
from tumnis.core.pagination import Page
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.versioning import NotFound
from tumnis.modules.knowledge import api
from tumnis.modules.projects import api as projects


async def _brief(s: AsyncSession, project_id: UUID) -> str | None:
    """The project's brief text; None before the `project.created` subscriber wrote it."""
    try:
        found = await api.get_brief(project_id, session=s)
    except NotFound:
        return None
    return found.body_md


projects.register_brief_source(_brief)


# --- Inputs ----------------------------------------------------------------------------------

Markdown = Annotated[str, StringConstraints(max_length=100_000)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Query = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class SearchKnowledgeIn(surface.SurfaceInput):
    """The tool's input and its twin's query."""

    q: Query
    project_id: UUID | None = None  # none: every project the caller sees, and the KB
    limit: int = Field(default=10, ge=1, le=api.SEARCH_LIMIT_MAX)
    cursor: str | None = Field(default=None, max_length=2048)


class GetDocumentIn(surface.SurfaceInput):
    document_id: UUID


class TextEntryIn(surface.SurfaceInput):
    """`add_document`'s twin body (P1-17's text entry, R-36)."""

    project_id: UUID | None = None  # None: the workspace knowledge base
    title: Title
    body_md: Markdown = ""
    tags: list[str] = Field(default=[], max_length=api.MAX_TAGS)


class AddDocumentIn(surface.WriteInput, TextEntryIn):
    pass


# --- Handlers --------------------------------------------------------------------------------


def _not_found() -> ProblemError:
    return ProblemError(404, "not_found", "Not found")


async def _search(call: surface.SurfaceCall, data: SearchKnowledgeIn) -> Page[api.KnowledgeHit]:
    return await api.search_page(
        call.session,
        data.q,
        project_id=data.project_id,
        limit=data.limit,
        cursor=data.cursor,
        project_ids=call.project_ids,
    )


async def _get(call: surface.SurfaceCall, data: GetDocumentIn) -> api.DocumentDTO:
    return await api.get_document(call.session, data.document_id)


async def _add(call: surface.SurfaceCall, data: AddDocumentIn) -> api.DocumentDTO:
    if data.project_id is None and call.project_ids is not None:
        raise _not_found()  # a project-limited caller never writes to the workspace KB (R-28)
    net = api.current_net()
    if call.caller.principal.kind == "session":
        return await api.create_text_entry(
            call.session, data.project_id, data.title, data.body_md, net=net, tags=data.tags
        )
    return await api.add_document(
        call.session,
        call.caller,
        project_id=data.project_id,
        title=data.title,
        body_markdown=data.body_md,
        tags=data.tags,
        now=call.now,
        net=net,
    )


async def _scope_of_document(ctx: WorkspaceContext, raw: Any) -> UUID | None:
    try:
        document_id = UUID(str(raw.get("document_id")))
    except ValueError:
        return None
    return await api.readable_scope(ctx, document_id)


# --- Ops -------------------------------------------------------------------------------------

SEARCH_KNOWLEDGE = surface.register_op(
    surface.SurfaceOp(
        name="search_knowledge",
        description=(
            "Full-text search of the knowledge base: a project's documents and the"
            " workspace-wide ones (without project_id, every project you can see). Each hit"
            " cites its document, heading path and page, best first; pages by cursor."
        ),
        scope="context:read",
        input_model=SearchKnowledgeIn,
        output_model=Page[api.KnowledgeHit],
        rest_method="GET",
        rest_path="/v1/knowledge/search",
        write=False,
        updates_existing=False,
        project_arg="project_id",
        project_resolver=None,
        handler=_search,
    )
)
GET_DOCUMENT = surface.register_op(
    surface.SurfaceOp(
        name="get_document",
        description=(
            "Read one knowledge-base document: its title, trust, tags and Markdown text (a"
            " file's text once it has been scanned and extracted). Cite a page of it in a"
            " result as a document link tumnis://doc/<id>#page=<n>."
        ),
        scope="context:read",
        input_model=GetDocumentIn,
        output_model=api.DocumentDTO,
        rest_method="GET",
        rest_path="/v1/knowledge/documents/{document_id}",
        write=False,
        updates_existing=False,
        project_arg=None,
        project_resolver=_scope_of_document,
        handler=_get,
    )
)
ADD_DOCUMENT = surface.register_op(
    surface.SurfaceOp(
        name="add_document",
        description=(
            "Add a Markdown document to a project's knowledge base. It is saved to the"
            " project's folder under agent-outputs/, searchable at once, and untrusted"
            " (marked as written by the agent) until a person reviews it."
        ),
        scope="knowledge:write",
        input_model=AddDocumentIn,
        output_model=api.DocumentDTO,
        rest_method="POST",
        rest_path="/v1/knowledge/documents/text",
        write=True,
        updates_existing=False,
        project_arg="project_id",
        project_resolver=None,
        handler=_add,
    )
)
