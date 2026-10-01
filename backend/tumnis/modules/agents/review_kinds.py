"""Review kinds the agents module queues (R-03), registered with P0-18's registry at import.

- `drift` (P2-10, SAF-2, SAF-3): a profile has an MCP server its project's allowlist does
  not name, or one of its tokens reaches another project's repo or app. Degraded profiles
  still run tasks; the item tells Scott what to fix in the profile (the app never writes
  into a profile's home, FR-5.12). Actions: `accept` (acknowledge) and `snooze`. The
  dedupe key hashes the finding, so the same drift on the next check queues nothing new.
- `result` (P2-04, FR-5.8): an agent's result for its task (summary, files touched,
  links). Accept moves the task to Done; reject needs `{feedback}`, which becomes a comment
  on the task, returns it to In progress and runs the agent again (`request_run` with
  `rerun_of`). `agents.apply_review_decision` applies both.
- `run_limit` (P2-04, SAF-5): a run stopped at its active-time cap or the wall-clock
  ceiling; the task stays In progress with the log. Accept acknowledges it.
- `question` (P2-05, FR-5.7): a run asks the human. `answer` needs `{answer}` (one of the
  choices when the question offers any). `approval` (P2-05, FR-5.6, SEC-3): a run asks to
  take an action; `approve` and `deny` need `{reason}` (blank is 422 `reason_required`).
  Their `on_decide` hook records the answer on the `questions` or `approvals` row and, for
  an approval, the `approval.granted` or `approval.denied` audit row with the reason, in
  the decide transaction; `agents.apply_review_decision` then wakes the waiting workflow.
- `delegation_loop` (P2-06, SAF-5): the master delegated the same task too often in the
  window without an accepted result, or back into its own chain; the delegation was
  refused and the master's runs that were active then were stopped. Actions: `accept`
  (acknowledge) and `snooze`.
"""

from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import Table, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import audit
from tumnis.core.errors import ProblemError
from tumnis.modules.agents.models import ApprovalRow, QuestionRow
from tumnis.modules.tasks import api as tasks

_questions: Table = QuestionRow.__table__  # type: ignore[assignment]
_approvals: Table = ApprovalRow.__table__  # type: ignore[assignment]


class ForeignReach(BaseModel):
    """A token reaching another project's repo (`github`) or app (`coolify`)."""

    kind: Literal["github", "coolify"]
    target: str = Field(max_length=200)
    project_id: UUID | None = None  # the project that links it
    project_name: str | None = None


class DriftPayload(BaseModel):
    profile_id: UUID
    profile: str
    extra: list[str] = Field(default=[], max_length=200)  # servers outside the allowlist
    foreign: list[ForeignReach] = Field(default=[], max_length=100)
    fix: str = Field(max_length=2000)  # what to change in the profile, in words


DRIFT: Final = tasks.ReviewKindSpec(
    kind="drift",
    owner_module="agents",
    payload_schema=DriftPayload,
    actions=("accept", "snooze"),
    impact_scope="project",
)
tasks.register_review_kind(DRIFT)


# --- Runs (P2-04, FR-5.8, SAF-5) --------------------------------------------------------------

RESULT: Final = "result"
RUN_LIMIT: Final = "run_limit"


class ResultPayload(tasks.ResultFields):
    """An agent's result waiting for the human: accept finishes the task, reject sends it
    back to the agent with the feedback (a comment on the task and a new run)."""

    run_id: UUID


class RejectFeedback(BaseModel):
    feedback: str = Field(min_length=1, max_length=8000, pattern=r"\S")


class RunLimitPayload(BaseModel):
    """A run stopped at a time limit (the active-time cap or the wall-clock ceiling); the
    task stays In progress with the log. Accept acknowledges it."""

    run_id: UUID
    task_id: UUID | None = None
    reason: str = Field(max_length=100)


tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=RESULT,
        owner_module="agents",
        payload_schema=ResultPayload,
        actions=("accept", "reject", "snooze"),
        impact_scope="task",
        action_payloads={"reject": RejectFeedback},
    )
)
tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=RUN_LIMIT,
        owner_module="agents",
        payload_schema=RunLimitPayload,
        actions=("accept", "snooze"),
        impact_scope="task",
    )
)


# --- Questions and approvals (P2-05, FR-5.6, FR-5.7, SEC-3) ---------------------------------

QUESTION: Final = "question"
APPROVAL: Final = "approval"
TEXT_MAX: Final = 4000


class QuestionPayload(BaseModel):
    question_id: UUID
    run_id: UUID
    prompt: str = Field(max_length=TEXT_MAX)
    choices: list[str] = Field(default=[], max_length=20)


class ApprovalPayload(BaseModel):
    approval_id: UUID
    run_id: UUID
    action_class: str = Field(max_length=200)
    description: str = Field(default="", max_length=TEXT_MAX)
    target: str | None = Field(default=None, max_length=500)
    rule: str = Field(max_length=60)


class QuestionAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=TEXT_MAX, pattern=r"\S")


class ApprovalReason(BaseModel):
    """Blank passes here, so the hook answers 422 `reason_required` (SEC-3)."""

    reason: str = Field(default="", max_length=2000)


async def _answered(s: AsyncSession, d: tasks.Deciding) -> str | None:
    question = QuestionPayload.model_validate(d.item_payload)
    answer = str((d.payload or {}).get("answer", "")).strip()
    if question.choices and answer not in question.choices:
        raise ProblemError(422, "invalid_answer", "Answer with one of the question's choices")
    await s.execute(
        update(_questions)
        .where(_questions.c.id == question.question_id, _questions.c.status == "pending")
        .values(status="answered", answer=answer, decided_by=str(d.actor), decided_at=d.now)
    )
    return None


async def _approval_decided(s: AsyncSession, d: tasks.Deciding) -> str | None:
    approval = ApprovalPayload.model_validate(d.item_payload)
    reason = str((d.payload or {}).get("reason") or "").strip()
    if not reason:
        raise ProblemError(422, "reason_required", "Approving or denying needs a reason")
    granted = d.action == "approve"
    await s.execute(
        update(_approvals)
        .where(_approvals.c.id == approval.approval_id, _approvals.c.status == "pending")
        .values(
            status="approved" if granted else "denied",
            reason=reason,
            decided_by=str(d.actor),
            decided_at=d.now,
        )
    )
    await audit.record(
        s,
        "approval.granted" if granted else "approval.denied",
        target=("approval", approval.approval_id),
        reason=reason,
        details={
            "action_class": approval.action_class,
            "rule": approval.rule,
            "run_id": str(approval.run_id),
        },
        occurred_at=d.now,
    )
    return reason


tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=QUESTION,
        owner_module="agents",
        payload_schema=QuestionPayload,
        actions=("answer", "snooze"),
        impact_scope="task",
        action_payloads={"answer": QuestionAnswer},
        on_decide=_answered,
    )
)
tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=APPROVAL,
        owner_module="agents",
        payload_schema=ApprovalPayload,
        actions=("approve", "deny", "snooze"),
        impact_scope="task",
        action_payloads={"approve": ApprovalReason, "deny": ApprovalReason},
        on_decide=_approval_decided,
    )
)


# --- Delegation (P2-06, SAF-5) ---------------------------------------------------------------

DELEGATION_LOOP_KIND: Final = "delegation_loop"


class DelegationLoopPayload(BaseModel):
    """A refused delegation: the task, how many times it was delegated in the window,
    whether it was a cycle (the task is in its own delegation chain), and the master's
    runs that were stopped."""

    task_id: UUID
    delegations: int = Field(ge=0)
    cycle: bool = False
    stopped_runs: list[UUID] = Field(default=[], max_length=100)


tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=DELEGATION_LOOP_KIND,
        owner_module="agents",
        payload_schema=DelegationLoopPayload,
        actions=("accept", "snooze"),
        impact_scope="task",
    )
)
