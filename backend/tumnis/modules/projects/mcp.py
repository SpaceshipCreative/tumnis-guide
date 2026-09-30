"""projects MCP tools; thin calls into api.py (P2-01, FR-14.10, R-33).

| Tool | Scope | REST twin |
| --- | --- | --- |
| `get_project_context` | tasks:read | `GET /v1/projects/{project_id}/context` |
"""

from uuid import UUID

from tumnis.core import agent_surface as surface
from tumnis.modules.projects import api


class GetProjectContextIn(surface.SurfaceInput):
    project_id: UUID


async def _context(call: surface.SurfaceCall, data: GetProjectContextIn) -> api.ProjectContextOut:
    return await api.project_context(call.session, data.project_id)


GET_PROJECT_CONTEXT = surface.register_op(
    surface.SurfaceOp(
        name="get_project_context",
        description=(
            "Read a project's context: name, client, goal, deadline, status, domains, code"
            " location (path or repository), the brief, the subtask card threshold and the"
            " approval policy."
        ),
        scope="tasks:read",
        input_model=GetProjectContextIn,
        output_model=api.ProjectContextOut,
        rest_method="GET",
        rest_path="/v1/projects/{project_id}/context",
        write=False,
        updates_existing=False,
        project_arg="project_id",
        project_resolver=None,
        handler=_context,
    )
)
