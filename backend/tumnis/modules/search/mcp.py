"""search MCP tools; thin calls into api.py (P2-01, FR-14.10, R-28, R-33).

| Tool | Scope | REST twin |
| --- | --- | --- |
| `search` | tasks:read | `GET /v1/search` |

A project-limited caller sees only rows of its projects; naming another project is 404.
"""

from uuid import UUID

from pydantic import Field

from tumnis.core import agent_surface as surface
from tumnis.core.pagination import LIMIT_DEFAULT, LIMIT_MAX, Page
from tumnis.modules.search import api


class SearchIn(surface.SurfaceInput):
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX)
    q: str = Field(default="", max_length=200)
    scope: api.Scope = "all"
    project_id: UUID | None = None  # boosts this project's rows


async def _search(call: surface.SurfaceCall, data: SearchIn) -> Page[api.SearchHit]:
    return await api.search(
        call.session,
        data.q,
        scope=data.scope,
        project_id=data.project_id,
        cursor=data.cursor,
        limit=data.limit,
        now=call.now,
        project_ids=call.project_ids,
    )


SEARCH = surface.register_op(
    surface.SurfaceOp(
        name="search",
        description=(
            "Search tasks and projects by text (phrases in quotes, prefixes), best match"
            " first; project_id boosts that project's rows. Pages by cursor."
        ),
        scope="tasks:read",
        input_model=SearchIn,
        output_model=Page[api.SearchHit],
        rest_method="GET",
        rest_path="/v1/search",
        write=False,
        updates_existing=False,
        project_arg="project_id",
        project_resolver=None,
        handler=_search,
    )
)
