"""agents MCP tools; thin calls into api.py (P2-02, FR-5.4, R-24; P2-03, FR-13.1, FR-13.4).

| Tool | Scope | REST twin |
| --- | --- | --- |
| `get_task_packet` | tasks:read | `GET /v1/tasks/{task_id}/packet` |
| `post_result` | tasks:write | `POST /v1/runs/{run_id}/result` |
| `ask_human` | tasks:write | `POST /v1/runs/{run_id}/questions` |
| `request_approval` | tasks:write | `POST /v1/runs/{run_id}/approvals` |
| `get_project_digest` | tasks:read | `GET /v1/digests/project/{project_id}` |
| `get_workspace_digest` | tasks:read | `GET /v1/digests/workspace` |

`get_task_packet` answers the task's packet as a run of the caller would get it, without a
token (`callback.task_token: null`): the token exists only in the packet a dispatch sends.
`post_result` (P2-04) stores the run's result with the run's own task token (403
`run_mismatch` for another run's); only an agent posts one, so its twin refuses a session.

The digests' consumer, whose cursor moves, is the caller's profile, or the API key when
the key belongs to no profile. Reading acknowledges the page named by `since` (P2-03).

agents also tells the agent surface which profile a caller acts for (`CallerFacts`): an
API key linked to a profile (`set_profile_key`) acts for that profile, and the master
profile's key is the master; a task token acts for its run's profile.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import Table, select

from tumnis.core import agent_surface as surface
from tumnis.core.errors import ProblemError
from tumnis.core.principal import Principal
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.modules.agents import api
from tumnis.modules.agents.models import AgentProfile, RunRow
from tumnis.modules.agents.packet_builder import (
    PacketTooLargeError,
    TaskPacket,
    enrich_packet,
    packet_for_caller,
)
from tumnis.modules.auth import api as auth
from tumnis.modules.tasks import api as tasks

_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_runs: Table = RunRow.__table__  # type: ignore[assignment]

PacketKind = Literal["task", "proposal", "stuck", "enrich"]


class TaskPacketQuery(surface.SurfaceInput):
    """The REST twin's query."""

    kind: PacketKind = "task"


class GetTaskPacketIn(TaskPacketQuery):
    task_id: UUID


async def _packet(call: surface.SurfaceCall, data: GetTaskPacketIn) -> TaskPacket:
    """Context items are outside content under `context:read` (FR-14.10): a caller with
    only `tasks:read` gets the packet without them. `kind=enrich` answers the enrich
    packet `enrich_task` would dispatch now (P1-17), with the configured run timeout."""
    if data.kind == "enrich":
        return await enrich_packet(
            call.session,
            data.task_id,
            run_id=call.caller.run_id,
            profile_id=call.caller.profile_id,
            timeout_s=api.enrichment_config().run_timeout_s,
        )
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
            " token), with outside text inside untrusted-data blocks. `kind` is task"
            " (default), proposal or stuck; enrich answers the enrichment request's packet"
            " with the brief and passages (nothing is dispatched)."
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


# --- Results (P2-04, FR-5.8) -----------------------------------------------------------------


class PostResultBody(surface.SurfaceInput, api.ResultFields):
    """The REST twin's body (the run is in the path)."""


class PostResultToolIn(surface.WriteInput, api.PostResultIn):
    pass


async def _post_result(call: surface.SurfaceCall, data: PostResultToolIn) -> api.ResultOut:
    inp = api.PostResultIn.model_validate(data.model_dump(exclude={"idempotency_key"}))
    return await api.accept_result(
        call.session, call.actor, call.caller.run_id, inp, now=call.now, tainted=call.tainted
    )


async def _project_of_run(ctx: WorkspaceContext, raw: Any) -> UUID | None:
    try:
        run_id = UUID(str(raw.get("run_id")))
    except ValueError:
        return None
    return await api.run_project(ctx, run_id)


POST_RESULT = surface.register_op(
    surface.SurfaceOp(
        name="post_result",
        description=(
            "Report the result of your run with its task token: outcome (done, partial or"
            " blocked), a summary, the files you touched and links (branch, pull request,"
            " document, draft or url). The task moves to In review for the human to accept"
            " or reject. Posting again for the same run returns the first result."
        ),
        scope="tasks:write",
        input_model=PostResultToolIn,
        output_model=api.ResultOut,
        rest_method="POST",
        rest_path="/v1/runs/{run_id}/result",
        write=True,
        updates_existing=False,
        project_arg=None,
        project_resolver=_project_of_run,
        handler=_post_result,
        session_twin_allowed=False,  # a result comes from the run's agent, never a session
    )
)


# --- Questions and approvals (P2-05, FR-5.6, FR-5.7) ---------------------------------------
#
# Both long-poll for the human after the call's transaction commits (`after_commit`), so
# their twins let `invoke` open the transaction (no route session). A key with no run
# answers `denied` with rule `run_token_required` (R-31).


class AskHumanBody(surface.SurfaceInput, api.HumanQuestion):
    """The REST twin's body (the run is in the path)."""


class AskHumanToolIn(surface.WriteInput, api.AskHumanIn):
    pass


class RequestApprovalBody(surface.SurfaceInput, api.HumanApproval):
    """The REST twin's body (the run is in the path)."""


class RequestApprovalToolIn(surface.WriteInput, api.RequestApprovalIn):
    pass


async def _ask_human(call: surface.SurfaceCall, data: AskHumanToolIn) -> api.HumanWaitOut:
    inp = api.AskHumanIn.model_validate(data.model_dump(exclude={"idempotency_key"}))
    return await api.ask_human(
        call.session,
        call.actor,
        call.caller.run_id,
        inp,
        caller_key=call.caller.key_id,
        tainted=call.tainted,
        now=call.now,
    )


async def _request_approval(
    call: surface.SurfaceCall, data: RequestApprovalToolIn
) -> api.HumanWaitOut:
    inp = api.RequestApprovalIn.model_validate(data.model_dump(exclude={"idempotency_key"}))
    return await api.request_approval(
        call.session,
        call.actor,
        call.caller.run_id,
        inp,
        caller_key=call.caller.key_id,
        tainted=call.tainted,
        now=call.now,
    )


def _poll(kind: Literal["question", "approval"]) -> surface.AfterCommit:
    async def poll(caller: surface.Caller, answer: Any) -> api.HumanWaitOut:
        ctx = caller.principal.workspace_context()
        return await api.long_poll_decision(ctx, kind, api.HumanWaitOut.model_validate(answer))

    return poll


ASK_HUMAN = surface.register_op(
    surface.SurfaceOp(
        name="ask_human",
        description=(
            "Ask the human a question with your run's task token; your task waits on the"
            " human until they answer, with no deadline. Waits up to 10 minutes for the"
            " answer; if it is still `pending`, call again later with `question_id` to get"
            " it. `choices` offers one-tap answers."
        ),
        scope="tasks:write",
        input_model=AskHumanToolIn,
        output_model=api.HumanWaitOut,
        rest_method="POST",
        rest_path="/v1/runs/{run_id}/questions",
        write=True,
        updates_existing=False,
        project_arg=None,
        project_resolver=_project_of_run,
        handler=_ask_human,
        session_twin_allowed=False,  # only a run's agent asks
        after_commit=_poll("question"),
    )
)
REQUEST_APPROVAL = surface.register_op(
    surface.SurfaceOp(
        name="request_approval",
        description=(
            "Ask before taking an action (action_class: a known class such as merge_main,"
            " push_main, deploy_production, send_email, delete_files, or a short name of"
            " your own), with your run's task token. The server decides: `approved` (go"
            " ahead), `denied` (do not), or `pending` (the human decides; call again with"
            " `approval_id`, after `retry_after_seconds` when set). Never proceed on"
            " `pending`."
        ),
        scope="tasks:write",
        input_model=RequestApprovalToolIn,
        output_model=api.HumanWaitOut,
        rest_method="POST",
        rest_path="/v1/runs/{run_id}/approvals",
        write=True,
        updates_existing=False,
        project_arg=None,
        project_resolver=_project_of_run,
        handler=_request_approval,
        session_twin_allowed=False,  # only a run's agent asks
        after_commit=_poll("approval"),
    )
)


# --- Digests (P2-03, FR-13.1, FR-13.4) ------------------------------------------------------

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
