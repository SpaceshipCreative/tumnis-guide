"""Delegation (P2-06, FR-5.2, SAF-5, design decisions 3, 5 and 6): the master hands a task
to its project's agent with `delegate_task`, and follows it with `wait_for_task`, which
never parks the master on a human.

- `delegate_task`: the task's project needs a provisioned agent (409
  `agent_not_provisioned`). Depth comes from the task's delegation chain, never from the
  caller (R-34): a task made by a delegated run's task token sits under that delegation,
  and a subtask under its parent's. Past `MAX_DELEGATION_DEPTH` the call is 409
  `delegation_depth_exceeded`. A loop (the third delegation of the task in the window
  without an accepted result, or a task in its own chain) is 409 `delegation_loop`: a
  separate transaction queues a `delegation_loop` review item and stops the master's
  active runs (stop reason `delegation_loop`), and no child run is made. Otherwise, in the
  call's transaction: the `delegations` row, the master's note as a comment on the task
  (an untrusted block in the child's packet, P2-02), and `request_run` with the run id
  preset to the delegation id, so the child's `dispatch_run` workflow ID is the
  delegation id. `request_run` refuses while agents are paused (409 `agents_paused`).
- `wait_for_task`: a read. The handler answers the child run's state; after that
  transaction commits, the wait follows the run's `state:<seq>` events (DBOS
  `get_event`, re-reading the row after every slice) until it is `done`,
  `waiting_on_human` or the timeout runs out (`still_running`).

This module imports `api`; `api` never imports it.
"""

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, PrivateAttr
from sqlalchemy import Table, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import agent_surface as surface
from tumnis.core import deadletter
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.versioning import NotFound
from tumnis.modules.agents import api
from tumnis.modules.agents.models import (
    AgentProfile,
    ApprovalRow,
    Delegation,
    QuestionRow,
    RunRow,
)
from tumnis.modules.agents.review_kinds import DELEGATION_LOOP_KIND, DelegationLoopPayload
from tumnis.modules.agents.rules import (
    DelegationRecord,
    RunStatus,
    WaitStatus,
    delegation_depth,
    depth_exceeded,
    is_delegation_loop,
    wait_status,
)
from tumnis.modules.auth import api as auth
from tumnis.modules.tasks import api as tasks

_log = logging.getLogger(__name__)
_delegations: Table = Delegation.__table__  # type: ignore[assignment]
_runs: Table = RunRow.__table__  # type: ignore[assignment]
_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_questions: Table = QuestionRow.__table__  # type: ignore[assignment]
_approvals: Table = ApprovalRow.__table__  # type: ignore[assignment]

NOTE_MAX: Final = 4000
WAIT_DEFAULT_S: Final = 600  # design decision 6: ten minutes
CHAIN_STEPS_MAX: Final = 10  # a chain is at most two delegations deep; this bounds the walk
EVENT_SLICE_S: Final = 30.0  # a running run: re-read the row at least this often
SHORT_SLICE_S: Final = 1.0  # queued or held: prepare and holds bump state_seq with no event
POLL_STEP_S: Final = 0.5  # the row poll when the DBOS client cannot be used
UNPROVISIONED: Final = frozenset({"not_provisioned", "provisioning"})
_TOKEN_AUTHOR: Final = "task_token:"  # noqa: S105  # an actor-ref prefix, not a secret


# --- Models (R-33) ---------------------------------------------------------------------------


class DelegateBody(surface.SurfaceInput):
    """`POST /v1/delegations`'s body."""

    task_id: UUID
    note: str | None = Field(
        default=None,
        max_length=NOTE_MAX,
        description="For the agent: added to the task as a comment it reads in its packet.",
    )


class DelegateIn(surface.WriteInput, DelegateBody):
    pass


class DelegateOut(BaseModel):
    schema_version: Literal[1] = 1
    delegation_id: UUID  # also the child run's id and its first workflow ID
    project_id: UUID
    depth: int
    tainted: bool  # written by a key with no run (R-31), or the task is tainted (P2-08)


class WaitQuery(surface.SurfaceInput):
    """`GET /v1/delegations/{delegation_id}/wait`'s query."""

    timeout_seconds: int = Field(default=WAIT_DEFAULT_S, ge=1, le=WAIT_DEFAULT_S)


class WaitIn(WaitQuery):
    delegation_id: UUID


class WaitOut(BaseModel):
    schema_version: Literal[1] = 1
    status: WaitStatus
    run_status: RunStatus
    question: str | None = None  # waiting_on_human: the question or the approval's prompt
    result_summary: str | None = None  # done and succeeded
    task_status: str
    # What the wait after the commit follows (never part of the answer).
    _delegation_id: UUID = PrivateAttr()
    _timeout_s: int = PrivateAttr(default=WAIT_DEFAULT_S)
    _workflow_id: str = PrivateAttr(default="")
    _seq: int = PrivateAttr(default=0)


# --- The chain and the history ---------------------------------------------------------------


def _record(row: Any) -> DelegationRecord:
    return DelegationRecord(
        delegation_id=row.id,
        task_id=row.child_task_id,
        delegated_at=row.delegated_at,
        accepted=row.accepted_at is not None,
        accepted_at=row.accepted_at,
    )


_COLUMNS: Final = (
    _delegations.c.id,
    _delegations.c.child_task_id,
    _delegations.c.delegated_at,
    _delegations.c.accepted_at,
)


async def _creator_delegation(s: AsyncSession, created_by: str) -> DelegationRecord | None:
    """The delegation whose run's task token wrote `created_by`, if any."""
    if not created_by.startswith(_TOKEN_AUTHOR):
        return None
    try:
        token_id = UUID(created_by.removeprefix(_TOKEN_AUTHOR))
    except ValueError:
        return None
    run_id = (await auth.task_token_runs(s, [token_id])).get(token_id)
    if run_id is None:
        return None
    delegation_id = await s.scalar(select(_runs.c.delegation_id).where(_runs.c.id == run_id))
    if delegation_id is None:
        return None
    row = (await s.execute(select(*_COLUMNS).where(_delegations.c.id == delegation_id))).first()
    return None if row is None else _record(row)


async def delegation_chain(s: AsyncSession, task_id: UUID) -> list[DelegationRecord]:
    """The delegations that produced the task and its ancestors, nearest first (R-34):
    from each task, the delegation whose run created it (then that delegation's task),
    else its parent; it stops at a task with neither, or one it has seen."""
    chain: list[DelegationRecord] = []
    seen = {task_id}
    current = task_id
    for _ in range(CHAIN_STEPS_MAX):
        try:
            created_by, parent_id = await tasks.task_creator(s, current)
        except NotFound:
            break
        found = await _creator_delegation(s, created_by)
        if found is not None:
            chain.append(found)
            following: UUID | None = found.task_id
        else:
            following = parent_id
        if following is None or following in seen:
            break
        seen.add(following)
        current = following
    return chain


async def _history(s: AsyncSession, task_id: UUID) -> list[DelegationRecord]:
    rows = await s.execute(
        select(*_COLUMNS)
        .where(_delegations.c.child_task_id == task_id, _delegations.c.deleted_at.is_(None))
        .order_by(_delegations.c.delegated_at, _delegations.c.id)
    )
    return [_record(row) for row in rows]


# --- delegate_task ---------------------------------------------------------------------------


async def _project_agent_status(s: AsyncSession, project_id: UUID) -> str | None:
    status: str | None = await s.scalar(
        select(_profiles.c.status).where(
            _profiles.c.role == "project",
            _profiles.c.project_id == project_id,
            _profiles.c.deleted_at.is_(None),
        )
    )
    return status


async def _callers_active_runs(
    s: AsyncSession, profile_id: UUID | None, run_id: UUID | None
) -> list[UUID]:
    """The caller's own active runs: its token's run, and the active `dispatch_run` (or
    not yet dispatched) runs of the profile its key acts for."""
    found: set[UUID] = set()
    if run_id is not None:
        found.add(run_id)
    if profile_id is not None:
        mine = select(_runs.c.id).where(
            _runs.c.profile_id == profile_id,
            _runs.c.status.in_(api.ACTIVE_RUN),
            _runs.c.deleted_at.is_(None),
        )
        found.update(await s.scalars(api.dispatched(mine)))
        found.update(await s.scalars(mine.where(_runs.c.workflow_id.is_(None))))
    return sorted(found, key=str)


async def _stop_loop(
    call: surface.SurfaceCall, task: tasks.TaskOut, history: list[DelegationRecord], cycle: bool
) -> None:
    """In its own transaction (the refusal rolls the call's back): the caller's active
    runs stopped with reason `delegation_loop`, and the `delegation_loop` review item."""
    ctx = call.caller.principal.workspace_context()
    async with tenant_session(ctx) as own:
        stopped = await _callers_active_runs(own, call.caller.profile_id, call.caller.run_id)
        for run_id in stopped:
            try:
                await api.cancel_run(
                    ctx, run_id, now=call.now, reason=api.DELEGATION_LOOP, session=own
                )
            except NotFound:
                continue
        latest = history[-1].delegation_id if history else task.id
        await tasks.add_review_item(
            DELEGATION_LOOP_KIND,
            target=tasks.TargetRef(type="task", id=task.id),
            project_id=task.project_id,
            payload=DelegationLoopPayload(
                task_id=task.id, delegations=len(history), cycle=cycle, stopped_runs=stopped
            ).model_dump(mode="json"),
            dedupe_key=f"delegation_loop:{task.id}:{latest}",
            session=own,
        )


async def delegate_task(call: surface.SurfaceCall, inp: DelegateIn) -> DelegateOut:
    """See the module docstring. 404 for a task the caller cannot see."""
    s = call.session
    task = await tasks.get_task(s, inp.task_id)
    status = await _project_agent_status(s, task.project_id)
    if status is None or status in UNPROVISIONED:
        raise ProblemError(
            409, "agent_not_provisioned", "The task's project has no provisioned agent yet"
        )
    chain = await delegation_chain(s, task.id)
    depth = delegation_depth(chain)
    if depth_exceeded(depth):
        raise ProblemError(
            409,
            "delegation_depth_exceeded",
            "This task is already two delegations deep; it cannot be delegated again",
        )
    history = await _history(s, task.id)
    if is_delegation_loop(history, task.id, call.now, chain=chain):
        cycle = any(record.task_id == task.id for record in chain)
        await _stop_loop(call, task, history, cycle)
        raise ProblemError(
            409,
            "delegation_loop",
            "This task was delegated again and again without an accepted result; it is in"
            " the review queue",
        )
    delegation_id = uuid7()
    tainted = call.tainted or task.tainted
    await s.execute(
        insert(_delegations).values(
            id=delegation_id,
            child_task_id=task.id,
            project_id=task.project_id,
            parent_run_id=call.caller.run_id,
            depth=depth,
            note=inp.note,
            delegated_at=call.now,
            tainted=tainted,
        )
    )
    if inp.note:
        await tasks.add_comment(s, call.actor, task.id, inp.note, now=call.now)
    await api.request_run(
        task.id,
        api.RunKind.TASK,
        delegation_id=delegation_id,
        ctx=call.caller.principal.workspace_context(),
        session=s,
        now=call.now,
    )
    return DelegateOut(
        delegation_id=delegation_id, project_id=task.project_id, depth=depth, tainted=tainted
    )


async def mark_accepted(s: AsyncSession, run_id: UUID, at: datetime) -> None:
    """The human accepted the run's result: a delegation's (its id is the run's) loop
    count starts again. A run no delegation made matches nothing."""
    await s.execute(
        update(_delegations)
        .where(_delegations.c.id == run_id, _delegations.c.accepted_at.is_(None))
        .values(accepted_at=at)
    )


async def delegation_project(ctx: WorkspaceContext, delegation_id: UUID) -> UUID | None:
    """The delegated task's project; None for a delegation the caller cannot see (the
    `wait_for_task` op's project)."""
    async with tenant_session(ctx) as s:
        found: UUID | None = await s.scalar(
            select(_delegations.c.project_id).where(
                _delegations.c.id == delegation_id, _delegations.c.deleted_at.is_(None)
            )
        )
    return found


# --- wait_for_task ---------------------------------------------------------------------------


async def _open_question(s: AsyncSession, run_id: UUID) -> str | None:
    """The newest question or approval that parked the run, as the human sees it."""
    prompt: str | None = await s.scalar(
        select(_questions.c.prompt)
        .where(
            _questions.c.run_id == run_id,
            _questions.c.status == "pending",
            _questions.c.review_item_id.is_not(None),
        )
        .order_by(_questions.c.created_at.desc())
        .limit(1)
    )
    if prompt is not None:
        return prompt
    approval = (
        await s.execute(
            select(_approvals.c.action_class, _approvals.c.description)
            .where(
                _approvals.c.run_id == run_id,
                _approvals.c.status == "pending",
                _approvals.c.review_item_id.is_not(None),
            )
            .order_by(_approvals.c.created_at.desc())
            .limit(1)
        )
    ).first()
    if approval is None:
        return None
    return str(approval.description or approval.action_class)


async def delegation_snapshot(s: AsyncSession, delegation_id: UUID, timeout_s: int) -> WaitOut:
    """The child run's state as `wait_for_task` answers it; 404 for an unknown delegation."""
    run = (
        await s.execute(
            select(_runs.c.status, _runs.c.state_seq, _runs.c.workflow_id, _runs.c.task_id).where(
                _runs.c.id == delegation_id,
                _runs.c.delegation_id == delegation_id,
                _runs.c.deleted_at.is_(None),
            )
        )
    ).first()
    if run is None:
        raise NotFound("delegations", delegation_id)
    run_status = RunStatus(run.status)
    status = wait_status(run_status)
    question = None
    if run_status is RunStatus.WAITING_ON_HUMAN:
        question = await _open_question(s, delegation_id)
        if question is None:  # answered; the run resumes once its workflow hears it
            status = "still_running"
    summary = None
    if run_status is RunStatus.SUCCEEDED:
        result = await tasks.result_of_run(s, delegation_id)
        summary = None if result is None else result.summary
    task_status = "deleted"  # the task was trashed since
    if run.task_id is not None:
        with contextlib.suppress(NotFound):
            task_status = (await tasks.get_task(s, run.task_id)).status.value
    out = WaitOut(
        status=status,
        run_status=run_status,
        question=question,
        result_summary=summary,
        task_status=task_status,
    )
    out._delegation_id = delegation_id  # the model's own private state
    out._timeout_s = timeout_s
    out._workflow_id = run.workflow_id or api.dispatch_workflow_id(delegation_id)
    out._seq = run.state_seq
    return out


async def wait_snapshot(call: surface.SurfaceCall, inp: WaitIn) -> WaitOut:
    """The op's handler: the state now, in the call's (read) transaction."""
    return await delegation_snapshot(call.session, inp.delegation_id, inp.timeout_seconds)


async def _await_change(snap: WaitOut, slice_s: float, events: bool) -> bool:
    """Waits up to `slice_s` for the run's next state event; False once the DBOS client
    fails, after which the caller polls the row."""
    if events:
        try:
            await deadletter.dbos_client().get_event_async(
                snap._workflow_id,
                f"state:{snap._seq + 1}",
                timeout_seconds=slice_s,
            )
            return True
        except Exception:  # any client failure falls back to the row poll
            _log.warning("wait_for_task: the DBOS client failed; polling the run row")
    await asyncio.sleep(min(POLL_STEP_S, slice_s))
    return False


async def wait_for_task(caller: surface.Caller, answer: BaseModel) -> WaitOut:
    """After the commit (no transaction held): follows the child run until it is done or
    waits on a human, or the timeout runs out (design decision 6). Each slice waits for
    the run's next `state:<seq>` event, then re-reads the row, so a run a deploy moved to
    a `supervise_run` continuation (a new workflow ID) is followed too."""
    if not isinstance(answer, WaitOut):  # pragma: no cover  # the handler's own answer
        raise TypeError("wait_for_task follows a WaitOut")
    snap = answer
    if snap.status != "still_running":
        return snap
    ctx = caller.principal.workspace_context()
    delegation_id = snap._delegation_id
    timeout_s = snap._timeout_s
    deadline = time.monotonic() + timeout_s
    events = True
    while snap.status == "still_running":
        left = deadline - time.monotonic()
        if left <= 0:
            break
        held = snap.run_status in (RunStatus.QUEUED, RunStatus.HELD)
        events = await _await_change(
            snap, min(left, SHORT_SLICE_S if held else EVENT_SLICE_S), events
        )
        async with tenant_session(ctx) as s:
            snap = await delegation_snapshot(s, delegation_id, timeout_s)
    return snap
