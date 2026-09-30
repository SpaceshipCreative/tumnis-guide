"""agents MCP tools; thin calls into api.py (P2-03, FR-13.1, FR-13.4, R-33).

| Tool | Scope | REST twin |
| --- | --- | --- |
| `get_project_digest` | tasks:read | `GET /v1/digests/project/{project_id}` |
| `get_workspace_digest` | tasks:read | `GET /v1/digests/workspace` |

The consumer whose cursor moves is the caller's profile, or the API key when the key
belongs to no profile. Reading acknowledges the page named by `since`.
"""

from uuid import UUID

from pydantic import Field

from tumnis.core import agent_surface as surface
from tumnis.core.errors import ProblemError
from tumnis.modules.agents import api

DEFAULT_LIMIT = 200  # plan default


class WorkspaceDigestIn(surface.SurfaceInput):
    since: str | None = Field(
        default=None,
        max_length=512,
        description="The `next_cursor` of the previous answer; it acknowledges that page.",
    )
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=1000)


class ProjectDigestIn(WorkspaceDigestIn):
    project_id: UUID


def _consumer(call: surface.SurfaceCall) -> UUID:
    consumer = call.caller.profile_id or call.caller.key_id
    if consumer is None:  # pragma: no cover  # authorize_call refused anonymous callers
        raise ProblemError(401, "unauthenticated", "Send an API key or a task token")
    return consumer


async def _project_digest(call: surface.SurfaceCall, data: ProjectDigestIn) -> api.DigestOut:
    return await api.read_digest(
        call.session,
        call.caller.principal.workspace_context(),
        consumer_id=_consumer(call),
        scope="project",
        project_id=data.project_id,
        since=data.since,
        limit=data.limit,
        project_ids=call.project_ids,
    )


async def _workspace_digest(call: surface.SurfaceCall, data: WorkspaceDigestIn) -> api.DigestOut:
    return await api.read_digest(
        call.session,
        call.caller.principal.workspace_context(),
        consumer_id=_consumer(call),
        scope="workspace",
        project_id=None,
        since=data.since,
        limit=data.limit,
        project_ids=call.project_ids,
    )


GET_PROJECT_DIGEST = surface.register_op(
    surface.SurfaceOp(
        name="get_project_digest",
        description=(
            "Read what changed in a project since your last acknowledged digest: human"
            " decisions (label overrides, results accepted or rejected with feedback,"
            " approvals, answers, accepted proposals), task changes and comments, estimate"
            " against actual time, knowledge edits and the full text of newly linked items."
            " Each entry comes once per caller: pass the previous answer's `next_cursor` as"
            " `since` to acknowledge it, and read again while `has_more` is true."
        ),
        scope="tasks:read",
        input_model=ProjectDigestIn,
        output_model=api.DigestOut,
        rest_method="GET",
        rest_path="/v1/digests/project/{project_id}",
        write=False,
        updates_existing=False,
        project_arg="project_id",
        project_resolver=None,
        handler=_project_digest,
    )
)

GET_WORKSPACE_DIGEST = surface.register_op(
    surface.SurfaceOp(
        name="get_workspace_digest",
        description=(
            "Read the workspace-wide signals since your last acknowledged digest: label"
            " overrides across projects, workspace knowledge base changes and focus setting"
            " changes. Pass the previous answer's `next_cursor` as `since` to acknowledge"
            " it, and read again while `has_more` is true."
        ),
        scope="tasks:read",
        input_model=WorkspaceDigestIn,
        output_model=api.DigestOut,
        rest_method="GET",
        rest_path="/v1/digests/workspace",
        write=False,
        updates_existing=False,
        project_arg=None,
        project_resolver=None,
        handler=_workspace_digest,
    )
)
