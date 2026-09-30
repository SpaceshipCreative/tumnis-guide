"""search FastAPI router: `GET /v1/search`, `GET /v1/typeahead/projects` and
`GET /v1/typeahead/tasks`; thin calls into api.py (P0-20, FR-3.9, A10).

A session or a key with `tasks:read` (A10). A project-limited key sees only rows of its
projects, and naming another project in `project_id` is 404 (R-28). `/v1/search` pages by
cursor; the typeaheads answer at most `limit` hits (8 by default), with no cursor. An
empty `q` is an empty answer.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request

from tumnis.core import agent_surface as surface
from tumnis.core.clock import Clock
from tumnis.core.idempotency import SessionDep
from tumnis.core.pagination import Page, PageParams, page_params
from tumnis.core.principal import principal_of
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.modules.search import api
from tumnis.modules.search import mcp as tools

router = v1_router("search", tags=["search"])

READ = frozenset({"tasks:read"})
Q = Annotated[str, Query(max_length=200, description="What the user typed")]
TYPEAHEAD_LIMIT = Annotated[int, Query(ge=1, le=20)]


def _now(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


@router.get("/search")
@route_policy(
    RoutePolicy(
        auth="session_or_key", scopes=READ, paginated=True, project_param="query:project_id"
    )
)
async def search(  # noqa: PLR0917  # the search tool's input as query parameters
    request: Request,
    session: SessionDep,
    page: Annotated[PageParams, Depends(page_params)],
    q: Q = "",
    scope: api.Scope = "all",
    project_id: Annotated[UUID | None, Query(description="Boosts this project's rows")] = None,
    schema_version: Annotated[int | None, Query()] = None,
) -> Page[api.SearchHit]:
    """Tasks and projects matching `q`, best first: text match, recency, project match.
    The `search` tool's twin (P2-01)."""
    raw = {
        "cursor": page.cursor,
        "limit": page.limit,
        "q": q,
        "scope": scope,
        "project_id": project_id,
        "schema_version": schema_version,
    }
    found = await surface.rest_twin(request, session, tools.SEARCH, raw)
    assert isinstance(found, Page)  # noqa: S101  # the op's output model
    return found


@router.get("/typeahead/projects")
@route_policy(
    RoutePolicy(
        auth="session_or_key",
        scopes=READ,
        unpaginated_reason="at most `limit` (20) best matches; a typeahead has no pages",
    )
)
async def typeahead_projects(
    request: Request, session: SessionDep, q: Q = "", limit: TYPEAHEAD_LIMIT = 8
) -> list[api.SearchHit]:
    """Live projects matching `q` (archived ones drop out), best first."""
    return await api.typeahead_projects(
        session, q, limit, now=_now(request).now(), project_ids=principal_of(request).project_ids
    )


@router.get("/typeahead/tasks")
@route_policy(
    RoutePolicy(
        auth="session_or_key",
        scopes=READ,
        project_param="query:project_id",
        unpaginated_reason="at most `limit` (20) best matches; a typeahead has no pages",
    )
)
async def typeahead_tasks(
    request: Request,
    session: SessionDep,
    q: Q = "",
    project_id: Annotated[UUID | None, Query(description="Boosts this project's tasks")] = None,
    limit: TYPEAHEAD_LIMIT = 8,
) -> list[api.SearchHit]:
    """Live tasks matching `q`, best first; the given project's tasks lead."""
    return await api.typeahead_tasks(
        session,
        q,
        project_id,
        limit,
        now=_now(request).now(),
        project_ids=principal_of(request).project_ids,
    )
