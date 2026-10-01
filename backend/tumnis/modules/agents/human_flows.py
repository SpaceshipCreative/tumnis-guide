"""Questions and approvals, the worker side (P2-05, FR-5.6, FR-5.7, R-30).

- `question_flow(workspace_id, question_id, resume)` and `approval_flow(workspace_id,
  approval_id, resume)` on the `human` queue (no concurrency limit: every flow parks on
  the human for as long as it takes). Workflow ids `question:<id>:<app version>` and
  `approval:<id>:<app version>`: DBOS 3.1.0 refuses a deduplication id on a partitioned
  queue and the id names the version that runs it, so a delivery or a deploy starts each
  version's flow once.
- An approval of a class the policy does not name is judged first (`evaluate_approval`,
  the approval-need decision point, P1-02): a confident "safe" approves it (`approval.auto`
  audit row); anything else opens it for the human (review item, task Waiting on human,
  `run.signal{waiting}`, `agent.gated_action`). Below the threshold or with Decisions
  down it never silently allows (FR-11.4).
- The flow then waits on topic `human` in `WAIT_SLICE_S` slices (no deadline), re-reading
  its row after each (the row is the truth; the message only wakes it), and closes:
  the task back In progress (as the system, once no other wait of the run is open) and
  `run.signal{resumed}`.
- `deliver_human_decision` (from `agents.apply_review_decision` on `human.decided`):
  wakes the row's flow when it is live on this version; otherwise (no flow yet, or one
  of an older version after a deploy) cancels the old one and starts `resume=True` on
  this version, which finds the decision on the row and closes.
"""

import asyncio
import contextvars
import logging
from typing import Any, Final
from uuid import UUID

from dbos import DBOS, SetWorkflowID
from sqlalchemy import Table, select, union_all, update

from tumnis.core import audit, faults
from tumnis.core.clock import SystemClock
from tumnis.core.outbox import emit
from tumnis.core.tenancy import WorkspaceContext, tenant_session, use_workspace
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.modules.agents import human
from tumnis.modules.agents.models import AgentProfile, ApprovalRow, QuestionRow, RunRow
from tumnis.modules.agents.payloads import RunSignalV1
from tumnis.modules.agents.review_kinds import APPROVAL, QUESTION
from tumnis.modules.agents.rules import NoulAnswer, approval_need
from tumnis.modules.agents.signals import stale_workflow
from tumnis.modules.decisions import api as decisions
from tumnis.modules.tasks import api as tasks

_log = logging.getLogger(__name__)
_runs: Table = RunRow.__table__  # type: ignore[assignment]
_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_questions: Table = QuestionRow.__table__  # type: ignore[assignment]
_approvals: Table = ApprovalRow.__table__  # type: ignore[assignment]
_TABLES: Final = {QUESTION: _questions, APPROVAL: _approvals}
DECIDED: Final = frozenset({"answered", "approved", "denied"})


def _ctx(workspace_id: str, actor: ActorRef = SYSTEM_ACTOR) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), actor)


def human_workflow_id(kind: str, wait_id: UUID | str, version: str | None = None) -> str:
    return f"{kind}:{wait_id}:{version or DBOS.application_version}"


# --- Steps ------------------------------------------------------------------------------------


@DBOS.step()
async def load_decision(workspace_id: str, kind: str, wait_id: str) -> str | None:
    """The wait's status once decided (answered, approved, denied); None while pending."""
    table = _TABLES[kind]
    async with tenant_session(_ctx(workspace_id)) as s:
        status = await s.scalar(select(table.c.status).where(table.c.id == UUID(wait_id)))
    return status if status in DECIDED else None


@DBOS.step()
async def evaluate_approval(workspace_id: str, approval_id: str) -> dict[str, str]:
    """Asks the approval-need decision point about an action the policy does not name,
    then applies the rule: allowed (the row approved, `approval.auto`) or opened for the
    human (`human.open_approval_in`). A row already judged answers as it stands."""
    wait = UUID(approval_id)
    async with tenant_session(_ctx(workspace_id)) as s:
        row = (await s.execute(select(_approvals).where(_approvals.c.id == wait))).one()
        run = (
            await s.execute(
                select(_runs.c.tainted, _runs.c.task_id, _profiles.c.project_id)
                .select_from(_runs.join(_profiles, _profiles.c.id == _runs.c.profile_id))
                .where(_runs.c.id == row.run_id)
            )
        ).one()
        policy = await human.policy_snapshot(s, run.project_id)
    if row.rule != human.UNKNOWN_NEEDS_DECISION:
        return {"status": row.status, "rule": row.rule}
    with use_workspace(_ctx(workspace_id)):
        try:
            need = await decisions.ask_approval_need(
                action_class=row.action_class,
                description=row.description,
                target=row.target,
                gated=policy.gated,
                allowed=policy.allowed,
                subject=decisions.SubjectRef(type="run", id=row.run_id),
                project_id=run.project_id,
            )
        except Exception:  # Decisions failing is Decisions down (FR-11.4)
            _log.warning("approval %s: the approval-need decision failed", approval_id)
            need = None
    noul = None if need is None else NoulAnswer(need.p, need.confidence, need.fallback)
    threshold = 1.0 if need is None else need.threshold
    verdict = approval_need(
        row.action_class, policy, run_tainted=run.tainted, noul=noul, threshold=threshold
    )
    now = SystemClock().now()
    async with tenant_session(_ctx(workspace_id, ActorRef(row.created_by))) as s:
        current = (
            await s.execute(select(_approvals).where(_approvals.c.id == wait).with_for_update())
        ).one()
        if current.rule != human.UNKNOWN_NEEDS_DECISION:  # judged by a replay already
            return {"status": current.status, "rule": current.rule}
        if verdict.outcome == "allowed":
            await s.execute(
                update(_approvals)
                .where(_approvals.c.id == wait)
                .values(status="approved", rule=verdict.rule, decided_by="system", decided_at=now)
            )
            await audit.record(
                s,
                "approval.auto",
                target=("approval", wait),
                details={"action_class": row.action_class, "rule": verdict.rule},
                occurred_at=now,
            )
            return {"status": "approved", "rule": verdict.rule}
        await human.open_approval_in(s, ActorRef(row.created_by), current, verdict.rule, now=now)
    return {"status": "pending", "rule": verdict.rule}


@DBOS.step()
async def close_human_wait(workspace_id: str, kind: str, wait_id: str) -> bool:
    """The human decided: once no other wait of the run is open, the task back In progress
    (as the system, W -> P) and `run.signal{resumed}`, in one transaction. Answers whether
    the run was resumed."""
    table = _TABLES[kind]
    wait = UUID(wait_id)
    now = SystemClock().now()
    async with tenant_session(_ctx(workspace_id)) as s:
        row = (
            await s.execute(select(table.c.run_id, table.c.task_id).where(table.c.id == wait))
        ).one()
        status = await s.scalar(
            select(_runs.c.status).where(_runs.c.id == row.run_id).with_for_update()
        )
        if status not in human.ACTIVE:
            return False
        others = (
            await s.execute(union_all(*human.open_waits_query(row.run_id, except_id=wait)))
        ).first()
        if others is not None:
            return False
        if row.task_id is not None:
            task = await tasks.get_task(s, row.task_id)
            if task.status == tasks.Status.WAITING_ON_HUMAN:
                await tasks.change_status(
                    s, SYSTEM_ACTOR, task.id, tasks.Status.IN_PROGRESS, task.version, now=now
                )
        await emit(s, RunSignalV1(run_id=row.run_id, kind="resumed"), occurred_at=now)
    return True


# --- Workflows --------------------------------------------------------------------------------


async def _wait_for_human(workspace_id: str, kind: str, wait_id: str) -> str:
    decision = await load_decision(workspace_id, kind, wait_id)
    faults.killpoint(f"agents.{kind}_flow.waiting")  # parked on the human
    while decision is None:  # no deadline (FR-5.6): re-armed every slice (R-30)
        await DBOS.recv_async(human.HUMAN_TOPIC, timeout_seconds=human.wait_slice_seconds())
        decision = await load_decision(workspace_id, kind, wait_id)
    await DBOS.set_event_async("decision", decision)
    await close_human_wait(workspace_id, kind, wait_id)
    return decision


@DBOS.workflow(name="question_flow")
async def question_flow(
    workspace_id: str, question_id: str, resume: bool = False
) -> str:  # the plan's signature
    """A run's question, from asked to answered (FR-5.7)."""
    del resume  # a question has nothing to evaluate: both paths wait on the row
    return await _wait_for_human(workspace_id, QUESTION, question_id)


@DBOS.workflow(name="approval_flow")
async def approval_flow(
    workspace_id: str, approval_id: str, resume: bool = False
) -> str:  # the plan's signature
    """A run's approval, from requested to decided (FR-5.6)."""
    if not resume:
        judged = await evaluate_approval(workspace_id, approval_id)
        if judged["status"] != "pending":
            await DBOS.set_event_async("decision", judged["status"])
            return judged["status"]
    return await _wait_for_human(workspace_id, APPROVAL, approval_id)


_FLOWS: Final = {QUESTION: question_flow, APPROVAL: approval_flow}


async def start_human_wait(
    workspace_id: UUID, kind: str, wait_id: UUID, *, resume: bool = False
) -> str:
    """Enqueues the wait's flow on the `human` queue under this version's id (a second
    start of the same id is the same workflow) and records it on the row. Started in a
    fresh context: a subscriber runs inside a DBOS step."""
    workflow_id = human_workflow_id(kind, wait_id)

    async def enqueue() -> None:
        with SetWorkflowID(workflow_id):
            await DBOS.enqueue_workflow_async(
                human.HUMAN_QUEUE, _FLOWS[kind], str(workspace_id), str(wait_id), resume
            )

    await asyncio.get_running_loop().create_task(enqueue(), context=contextvars.Context())
    table = _TABLES[kind]
    async with tenant_session(_ctx(str(workspace_id))) as s:
        await s.execute(update(table).where(table.c.id == wait_id).values(workflow_id=workflow_id))
    return workflow_id


async def deliver_human_decision(
    workspace_id: UUID, kind: str, item_id: UUID, decision: str, *, key: str
) -> None:
    """Wakes the wait's flow with the human's decision (the row already holds it). A flow
    of an older application version is cancelled and the wait resumed on this one."""
    table = _TABLES[kind]
    async with tenant_session(_ctx(str(workspace_id))) as s:
        row = (
            await s.execute(
                select(table.c.id, table.c.workflow_id).where(table.c.review_item_id == item_id)
            )
        ).first()
    if row is None:
        return
    stale = await stale_workflow(row.workflow_id)
    if stale is False:
        await DBOS.send_async(
            row.workflow_id, {"decision": decision}, topic=human.HUMAN_TOPIC, idempotency_key=key
        )
        return
    if stale and row.workflow_id is not None:
        await DBOS.cancel_workflow_async(row.workflow_id)
        _log.info("%s %s: flow %s of an older version cancelled", kind, row.id, row.workflow_id)
    await start_human_wait(workspace_id, kind, row.id, resume=True)


def resume_after_human(workspace_id: UUID, kind: str, wait_id: UUID) -> Any:
    """The plan's name for the resume path: start the wait's flow with `resume=True`."""
    return start_human_wait(workspace_id, kind, wait_id, resume=True)
