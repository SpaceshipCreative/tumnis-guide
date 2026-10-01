"""Questions and approvals, the api side (P2-05, FR-5.6, FR-5.7, SAF-1, SEC-3).

An agent asks the human (`ask_human`) or asks to take an action (`request_approval`) with
its run's task token. Every wait is a row (the truth: `questions`, `approvals`) and a
workflow (the reaction: `question_flow`, `approval_flow` on the `human` queue, started by
`agents.start_human_wait` from `question.asked` / `approval.requested`). The request
never calls DBOS: it writes the row, the review item, the task's move to Waiting on human,
`run.signal{waiting}`, the audit row and the event in one transaction, then long-polls the
row after the commit (`long_poll_decision`).

- The server decides what needs approval (`rules.approval_need`): gated classes always,
  everything on a tainted run (SAF-1), allowed classes go through (`read` on a clean run
  with no row; every other allowed class with an `approved` row and an `approval.auto`
  audit row). An action the policy does not name is `pending` with
  `retry_after_seconds` until `approval_flow` has asked Decisions (the api never calls
  out, architecture principle 3).
- A key with no run (R-31) can never park a run or take a gated action: both ops answer
  200 `denied` with rule `run_token_required` (the P2-01 sweeps hold every write op of a
  key with all scopes to a 2xx).
- A re-send (`question_id` / `approval_id`) reads the row first: a decided wait answers at
  once; a pending one long-polls again.
"""

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, Field
from sqlalchemy import Table, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import audit
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.limits import WAIT_SLICE_S
from tumnis.core.outbox import emit
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import ActorRef
from tumnis.core.versioning import NotFound
from tumnis.modules.agents.models import AgentProfile, ApprovalRow, QuestionRow, RunRow
from tumnis.modules.agents.payloads import (
    PROMPT_MAX,
    ApprovalRequestedV1,
    QuestionAskedV1,
    RunSignalV1,
)
from tumnis.modules.agents.review_kinds import (
    APPROVAL,
    QUESTION,
    ApprovalPayload,
    QuestionPayload,
    project_of_run,
)
from tumnis.modules.agents.rules import (
    DEFAULT_POLICY,
    READ_ACTION,
    PolicySnapshot,
    PolicyVerdict,
    RunStatus,
    approval_need,
)
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks

_runs: Table = RunRow.__table__  # type: ignore[assignment]
_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_questions: Table = QuestionRow.__table__  # type: ignore[assignment]
_approvals: Table = ApprovalRow.__table__  # type: ignore[assignment]

HUMAN_QUEUE: Final = "human"  # question_flow and approval_flow; no concurrency limit
HUMAN_TOPIC: Final = "human"  # the decision reaches the waiting flow on this topic
POLL_SECONDS_DEFAULT: Final = 600.0  # architecture: a 10-minute long poll
POLL_STEP_S: Final = 0.5  # how often the long poll re-reads the row
RETRY_AFTER_S: Final = 2  # plan default, while Decisions has yet to judge the action
RUN_TOKEN_REQUIRED: Final = "run_token_required"  # noqa: S105  # a rule name, no secret
UNKNOWN_NEEDS_DECISION: Final = "unknown_needs_decision"
_DENIED_NS: Final = UUID("2f6a3b1c-8d4e-5f70-9a1b-3c5d7e9f0a12")  # ids of keyless answers
WaitKind = Literal["question", "approval"]
ACTIVE: Final = (RunStatus.RUNNING.value, RunStatus.WAITING_ON_HUMAN.value)


@dataclass
class _Waits:
    poll_seconds: float = POLL_SECONDS_DEFAULT
    slice_seconds: float = WAIT_SLICE_S


_waits = _Waits()


def configure_human_waits(
    *, poll_seconds: float | None = None, wait_slice_seconds: float | None = None
) -> None:
    """Sets the long poll of `ask_human` / `request_approval` and the re-arm interval of
    the human waits (R-30) for this process (settings at startup; tests). Called with
    neither, it restores the defaults (10 minutes, `WAIT_SLICE_S`)."""
    if poll_seconds is None and wait_slice_seconds is None:
        _waits.poll_seconds, _waits.slice_seconds = POLL_SECONDS_DEFAULT, WAIT_SLICE_S
        return
    if poll_seconds is not None:
        _waits.poll_seconds = max(float(poll_seconds), 0.0)
    if wait_slice_seconds is not None:
        _waits.slice_seconds = max(float(wait_slice_seconds), 0.1)


def wait_slice_seconds() -> float:
    return _waits.slice_seconds


# --- Models (R-33) ---------------------------------------------------------------------------


class HumanQuestion(BaseModel):
    prompt: str = Field(min_length=1, max_length=PROMPT_MAX)
    choices: list[str] = Field(default=[], max_length=20)  # optional one-tap answers
    question_id: UUID | None = None  # set on re-send


class AskHumanIn(HumanQuestion):
    run_id: UUID


class HumanApproval(BaseModel):
    action_class: str = Field(min_length=1, max_length=200)  # a known class or a free name
    description: str = Field(max_length=PROMPT_MAX)
    target: str | None = Field(default=None, max_length=500)  # branch, app, VM, address
    approval_id: UUID | None = None  # set on re-send


class RequestApprovalIn(HumanApproval):
    run_id: UUID


class HumanWaitOut(BaseModel):
    schema_version: Literal[1] = 1
    status: Literal["pending", "answered", "approved", "denied"]
    id: UUID  # question_id or approval_id
    answer: str | None = None
    reason: str | None = None
    rule: str | None = None  # PolicyVerdict.rule for approvals
    retry_after_seconds: int | None = None
    tainted: bool = False  # the run (or a keyless caller) is tainted (P2-08, SAF-1)


@dataclass(frozen=True)
class Allowed:
    rule: str


@dataclass(frozen=True)
class ApprovalRequired:
    rule: str


@dataclass(frozen=True)
class Denied:
    code: Literal["run_token_required", "run_not_active", "agents_paused"]


# --- Reads -----------------------------------------------------------------------------------


def _denied(kind: WaitKind, caller_key: UUID | None, run_id: UUID) -> HumanWaitOut:
    """A keyless call's answer; its id is derived from the caller and the run, so a retry
    answers the same."""
    wait_id = uuid5(_DENIED_NS, f"{kind}:{caller_key}:{run_id}")
    return HumanWaitOut(status="denied", id=wait_id, rule=RUN_TOKEN_REQUIRED, tainted=True)


def _question_out(row: Any) -> HumanWaitOut:
    return HumanWaitOut(
        status="answered" if row.status == "answered" else "pending",
        id=row.id,
        answer=row.answer,
    )


def _approval_out(row: Any, tainted: bool) -> HumanWaitOut:
    return HumanWaitOut(
        status=row.status,
        id=row.id,
        reason=row.reason,
        rule=row.rule,
        retry_after_seconds=RETRY_AFTER_S if row.rule == UNKNOWN_NEEDS_DECISION else None,
        tainted=tainted,
    )


async def _run(s: AsyncSession, run_id: UUID) -> Any:
    run = (
        await s.execute(
            select(_runs.c.status, _runs.c.task_id, _runs.c.tainted, _profiles.c.project_id)
            .select_from(_runs.join(_profiles, _profiles.c.id == _runs.c.profile_id))
            .where(_runs.c.id == run_id, _runs.c.deleted_at.is_(None))
            .with_for_update(of=_runs)
        )
    ).first()
    if run is None:
        raise NotFound("runs", run_id)
    return run


async def policy_snapshot(s: AsyncSession, project_id: UUID | None) -> PolicySnapshot:
    """The project's approval policy; the default for a run with no project (master)."""
    if project_id is None:
        return DEFAULT_POLICY
    try:
        policy = await projects.get_policy(s, project_id)
    except NotFound:
        return DEFAULT_POLICY
    return PolicySnapshot(gated=frozenset(policy.gated), allowed=frozenset(policy.allowed))


def _check_run(caller_run: UUID, run_id: UUID) -> None:
    if caller_run != run_id:
        raise ProblemError(403, "run_mismatch", "This token belongs to another run")


async def _wait_row(s: AsyncSession, table: Table, wait_id: UUID, run_id: UUID) -> Any:
    row = (
        await s.execute(select(table).where(table.c.id == wait_id, table.c.run_id == run_id))
    ).first()
    if row is None:
        raise NotFound(table.name, wait_id)
    return row


# --- Opening a wait --------------------------------------------------------------------------


async def park_task(s: AsyncSession, actor: ActorRef, task_id: UUID | None, now: Any) -> None:
    """The run's task In progress -> Waiting on human, as the run's agent (FR-5.7); a task
    already waiting (a second open question) or elsewhere is left as it is."""
    if task_id is None:
        return
    task = await tasks.get_task(s, task_id)
    if task.status == tasks.Status.IN_PROGRESS:
        await tasks.change_status(
            s, actor, task_id, tasks.Status.WAITING_ON_HUMAN, task.version, now=now
        )


def _target(run_id: UUID, task_id: UUID | None) -> tasks.TargetRef:
    return (
        tasks.TargetRef(type="task", id=task_id)
        if task_id
        else tasks.TargetRef(type="run", id=run_id)
    )


async def open_approval_in(  # the approval's facts, spelled out
    s: AsyncSession,
    actor: ActorRef,
    row: Any,
    rule: str,
    *,
    now: Any,
) -> None:
    """Asks the human about an approval, in the caller's transaction: the `approval`
    review item, the task Waiting on human, `run.signal{waiting}` and one
    `agent.gated_action` audit row (SEC-3), as the run's agent."""
    payload = ApprovalPayload(
        approval_id=row.id,
        run_id=row.run_id,
        action_class=row.action_class,
        description=row.description,
        target=row.target,
        rule=rule,
    )
    item_id = await tasks.add_review_item(
        APPROVAL,
        target=_target(row.run_id, row.task_id),
        project_id=None,
        payload=payload.model_dump(mode="json"),
        dedupe_key=f"approval:{row.id}",
        session=s,
    )
    await s.execute(
        update(_approvals)
        .where(_approvals.c.id == row.id)
        .values(review_item_id=item_id, rule=rule)
    )
    await park_task(s, actor, row.task_id, now)
    await emit(s, RunSignalV1(run_id=row.run_id, kind="waiting"), occurred_at=now)
    await audit.record(
        s,
        "agent.gated_action",
        target=("approval", row.id),
        details={"action_class": row.action_class, "rule": rule, "run_id": str(row.run_id)},
        occurred_at=now,
        project_id=await project_of_run(s, row.run_id),
    )


async def ask_human(  # the call's facts, spelled out
    s: AsyncSession,
    actor: ActorRef,
    caller_run: UUID | None,
    inp: AskHumanIn,
    *,
    caller_key: UUID | None,
    tainted: bool,
    now: Any,
) -> HumanWaitOut:
    """First call: the question row, its `question` review item, the task Waiting on
    human, `run.signal{waiting}` and `question.asked`, in the caller's transaction; a
    re-send answers the row. 403 `run_mismatch` for another run's token, 409
    `run_not_active` once the run ended."""
    if caller_run is None:
        return _denied(QUESTION, caller_key, inp.run_id)
    _check_run(caller_run, inp.run_id)
    if inp.question_id is not None:
        return _question_out(await _wait_row(s, _questions, inp.question_id, inp.run_id))
    run = await _run(s, inp.run_id)
    if run.status not in ACTIVE:
        raise ProblemError(409, "run_not_active", f"The run is {run.status}")
    question_id = uuid7()
    await s.execute(
        insert(_questions).values(
            id=question_id,
            run_id=inp.run_id,
            task_id=run.task_id,
            prompt=inp.prompt,
            choices=inp.choices,
        )
    )
    item_id = await tasks.add_review_item(
        QUESTION,
        target=_target(inp.run_id, run.task_id),
        project_id=None,
        payload=QuestionPayload(
            question_id=question_id, run_id=inp.run_id, prompt=inp.prompt, choices=inp.choices
        ).model_dump(mode="json"),
        dedupe_key=f"question:{question_id}",
        session=s,
    )
    await s.execute(
        update(_questions).where(_questions.c.id == question_id).values(review_item_id=item_id)
    )
    await park_task(s, actor, run.task_id, now)
    await emit(s, RunSignalV1(run_id=inp.run_id, kind="waiting"), occurred_at=now)
    await emit(
        s,
        QuestionAskedV1(
            question_id=question_id, run_id=inp.run_id, task_id=run.task_id, prompt=inp.prompt
        ),
        occurred_at=now,
    )
    return HumanWaitOut(status="pending", id=question_id, tainted=tainted or run.tainted)


async def request_approval(  # the call's facts, spelled out
    s: AsyncSession,
    actor: ActorRef,
    caller_run: UUID | None,
    inp: RequestApprovalIn,
    *,
    caller_key: UUID | None,
    tainted: bool,
    now: Any,
) -> HumanWaitOut:
    """Task token only (R-31). First call: `approval_need(...)` with no Decisions answer
    (threshold None). Allowed: `approved` at once (no row for `read`; an `approved` row
    and `approval.auto` otherwise). Otherwise a `pending` row and `approval.requested`;
    a known verdict also opens the review item (`open_approval_in`) in this transaction,
    an unknown class waits for `approval_flow` to ask Decisions. A re-send answers the
    row."""
    if caller_run is None:
        return _denied(APPROVAL, caller_key, inp.run_id)
    _check_run(caller_run, inp.run_id)
    if inp.approval_id is not None:
        row = await _wait_row(s, _approvals, inp.approval_id, inp.run_id)
        return _approval_out(row, tainted)
    run = await _run(s, inp.run_id)
    if run.status not in ACTIVE:
        raise ProblemError(409, "run_not_active", f"The run is {run.status}")
    run_tainted = tainted or bool(run.tainted)
    policy = await policy_snapshot(s, run.project_id)
    verdict = approval_need(
        inp.action_class, policy, run_tainted=run_tainted, noul=None, threshold=None
    )
    approval_id = uuid7()
    if verdict.outcome == "allowed" and inp.action_class == READ_ACTION:
        return HumanWaitOut(status="approved", id=approval_id, rule=verdict.rule)
    allowed = verdict.outcome == "allowed"
    await s.execute(
        insert(_approvals).values(
            id=approval_id,
            run_id=inp.run_id,
            task_id=run.task_id,
            action_class=inp.action_class,
            description=inp.description,
            target=inp.target,
            rule=verdict.rule,
            status="approved" if allowed else "pending",
            decided_by="system" if allowed else None,
            decided_at=now if allowed else None,
        )
    )
    row = await _wait_row(s, _approvals, approval_id, inp.run_id)
    if allowed:
        await audit.record(
            s,
            "approval.auto",
            target=("approval", approval_id),
            details={"action_class": inp.action_class, "rule": verdict.rule},
            occurred_at=now,
        )
        return _approval_out(row, run_tainted)
    opened = verdict.rule != UNKNOWN_NEEDS_DECISION
    if opened:
        await open_approval_in(s, actor, row, verdict.rule, now=now)
    await emit(
        s,
        ApprovalRequestedV1(
            approval_id=approval_id,
            run_id=inp.run_id,
            task_id=run.task_id,
            action_class=inp.action_class,
            rule=verdict.rule,
            opened=opened,
        ),
        occurred_at=now,
    )
    return _approval_out(row, run_tainted)


async def check_action(
    ctx: WorkspaceContext, caller_run: UUID | None, action_class: str, *, tainted: bool = False
) -> Allowed | ApprovalRequired | Denied:
    """The one server-side policy check (R-33). Denied when the caller has no run (R-31)
    or the run is not active; otherwise the project's policy and the run's taint through
    `approval_need` (threshold None: unknown classes are judged by `approval_flow`)."""
    if caller_run is None:
        return Denied("run_token_required")
    async with tenant_session(ctx) as s:
        run = await _run(s, caller_run)
        if run.status not in ACTIVE:
            return Denied("run_not_active")
        verdict: PolicyVerdict = approval_need(
            action_class,
            await policy_snapshot(s, run.project_id),
            run_tainted=tainted or bool(run.tainted),
            noul=None,
            threshold=None,
        )
    if verdict.outcome == "allowed":
        return Allowed(verdict.rule)
    return ApprovalRequired(verdict.rule)


# --- The long poll ---------------------------------------------------------------------------


async def _read(ctx: WorkspaceContext, kind: WaitKind, out: HumanWaitOut) -> HumanWaitOut:
    table = _questions if kind == QUESTION else _approvals
    async with tenant_session(ctx) as s:
        row = (await s.execute(select(table).where(table.c.id == out.id))).first()
    if row is None:  # `read` approved with no row, or a keyless answer
        return out
    if kind == QUESTION:
        return _question_out(row).model_copy(update={"tainted": out.tainted})
    return _approval_out(row, out.tainted)


async def long_poll_decision(
    ctx: WorkspaceContext, kind: WaitKind, out: HumanWaitOut
) -> HumanWaitOut:
    """After the commit: re-reads the wait's row (the truth) every half second for up to
    the long poll (`configure_human_waits`, 10 minutes by default), answering as soon as
    it is decided, else the row as it stands (`pending`, and its id for the re-send)."""
    if out.status != "pending":
        return out
    deadline = time.monotonic() + _waits.poll_seconds
    while True:
        fresh = await _read(ctx, kind, out)
        left = deadline - time.monotonic()
        if fresh.status != "pending" or left <= 0:
            return fresh
        await asyncio.sleep(min(POLL_STEP_S, left))


def open_waits_query(run_id: UUID, *, except_id: UUID | None = None) -> Iterable[Any]:
    """Selects of the run's pending questions and approvals (other than `except_id`) that
    were opened for the human (a review item; they parked the run). An approval still
    waiting for Decisions (`unknown_needs_decision`) has none: it parks the run only if
    `open_approval_in` opens it later, and an auto-approved one never does."""
    found = []
    for table in (_questions, _approvals):
        stmt = select(table.c.id).where(
            table.c.run_id == run_id,
            table.c.status == "pending",
            table.c.review_item_id.is_not(None),
        )
        if except_id is not None:
            stmt = stmt.where(table.c.id != except_id)
        found.append(stmt)
    return found
