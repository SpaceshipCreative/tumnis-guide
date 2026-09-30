"""agents MCP tools; thin calls into api.py (P2-02, FR-5.4, R-24).

| Tool | Scope | REST twin |
| --- | --- | --- |
| `get_task_packet` | tasks:read | `GET /v1/tasks/{task_id}/packet` |

`get_task_packet` answers the task's packet as a run of the caller would get it, without a
token (`callback.task_token: null`): the token exists only in the packet a dispatch sends.

agents also tells the agent surface which profile a caller acts for (`CallerFacts`): an
API key linked to a profile (`set_profile_key`) acts for that profile, and the master
profile's key is the master; a task token acts for its run's profile.
"""

from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Table, select

from tumnis.core import agent_surface as surface
from tumnis.core.errors import ProblemError
from tumnis.core.principal import Principal
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.modules.agents import api
from tumnis.modules.agents.models import AgentProfile, RunRow
from tumnis.modules.agents.packet_builder import PacketTooLargeError, TaskPacket, packet_for_caller
from tumnis.modules.auth import api as auth
from tumnis.modules.tasks import api as tasks

_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_runs: Table = RunRow.__table__  # type: ignore[assignment]

PacketKind = Literal["task", "proposal", "stuck"]


class TaskPacketQuery(surface.SurfaceInput):
    """The REST twin's query."""

    kind: PacketKind = "task"


class GetTaskPacketIn(TaskPacketQuery):
    task_id: UUID


async def _packet(call: surface.SurfaceCall, data: GetTaskPacketIn) -> TaskPacket:
    """Context items are outside content under `context:read` (FR-14.10): a caller with
    only `tasks:read` gets the packet without them."""
    try:
        return await packet_for_caller(
            call.session,
            data.task_id,
            kind=api.RunKind(data.kind),
            run_id=call.caller.run_id,
            profile_id=call.caller.profile_id,
            with_context="context:read" in call.caller.scopes,
        )
    except PacketTooLargeError as exc:
        raise ProblemError(422, "packet_too_large", str(exc)) from None


async def _project_of_task(ctx: WorkspaceContext, raw: Any) -> UUID | None:
    try:
        task_id = UUID(str(raw.get("task_id")))
    except ValueError:
        return None
    return await tasks.project_of(ctx, task_id)


GET_TASK_PACKET = surface.register_op(
    surface.SurfaceOp(
        name="get_task_packet",
        description=(
            "The task's packet as a run would get it: the task, its project's brief and"
            " passages, context items (with context:read), the policy and the callback (no"
            " token), with outside"
            " text inside untrusted-data blocks. `kind` is task (default), proposal or stuck."
        ),
        scope="tasks:read",
        input_model=GetTaskPacketIn,
        output_model=TaskPacket,
        rest_method="GET",
        rest_path="/v1/tasks/{task_id}/packet",
        write=False,
        updates_existing=False,
        project_arg=None,
        project_resolver=_project_of_task,
        handler=_packet,
    )
)


# --- Caller facts ----------------------------------------------------------------------------


async def _profile_facts(principal: Principal) -> surface.CallerFacts | None:
    """The profile an API key is linked to (and whether it is the master's), or a task
    token's run's profile and taint; None for any other caller."""
    if principal.kind == "api_key" and principal.subject_id is not None:
        async with tenant_session(principal.workspace_context()) as s:
            row = (
                await s.execute(
                    select(_profiles.c.id, _profiles.c.role).where(
                        _profiles.c.api_key_id == principal.subject_id,
                        _profiles.c.deleted_at.is_(None),
                    )
                )
            ).first()
        if row is None:
            return None
        return surface.CallerFacts(profile_id=row.id, is_master=row.role == "master")
    run_id = await auth.task_token_run(principal)
    if run_id is None:
        return None
    async with tenant_session(principal.workspace_context()) as s:
        run = (
            await s.execute(select(_runs.c.profile_id, _runs.c.tainted).where(_runs.c.id == run_id))
        ).first()
    if run is None:
        return None
    # P2-08: what a tainted run writes is tainted (SAF-1).
    return surface.CallerFacts(profile_id=run.profile_id, run_tainted=run.tainted)


surface.register_caller_facts("agents.profile", _profile_facts)
