"""agents event payload models (P2-04, R-06); `events.py` holds the subscribers. They
live apart so `api.py` and `workflows.py` can emit them while `events.py` calls `api.py`.

- `run.requested`: a run row was made (status queued); the `agents.start_dispatch`
  subscriber enqueues its `dispatch_run` workflow on the `runs` queue.
- `run.signal`: something the run's workflow must hear (a result arrived, cancel, the
  runner was lost, a limit hit, waiting on a human, resumed, the agent failed). A request
  never calls `DBOS.send` itself: it writes its rows and this event in one transaction, and
  `agents.deliver_run_signal` sends it with the event id as the idempotency key.
- `run.started`: the run left the queue (queued or held -> running).
- `run.finished`: the run ended, once per run, with its terminal status and stop reason.
- `agents.paused` (P2-09): the kill switch, for the workspace or one project; the
  `agents.cancel_paused_runs` subscriber sends `run.signal{cancel}` to each running or
  waiting run in scope.
- `agents.resumed` (P2-09): a person resumed it in the app; the `agents.release_held_runs`
  subscriber sends `release` to the held runs nothing holds any more.
"""

from datetime import datetime
from typing import ClassVar, Final, Literal
from uuid import UUID

from pydantic import Field

from tumnis.core.events import EventPayload, event_type
from tumnis.modules.agents.rules import RunKind

SignalKind = Literal[
    "result", "cancel", "limit", "waiting", "resumed", "runner_lost", "agent_failed"
]
TerminalStatus = Literal["succeeded", "failed", "cancelled", "timed_out", "runner_lost"]
REASON_MAX: Final = 500


@event_type("run.requested", 1)
class RunRequestedV1(EventPayload):
    event_name: ClassVar[str] = "run.requested"
    schema_version: Literal[1] = 1
    run_id: UUID
    task_id: UUID
    project_id: UUID
    kind: RunKind
    priority: int | None = None  # lower runs first (DBOS queue priority)
    unattended: bool = False
    rerun_of: UUID | None = None


@event_type("run.signal", 1)
class RunSignalV1(EventPayload):
    event_name: ClassVar[str] = "run.signal"
    schema_version: Literal[1] = 1
    run_id: UUID
    kind: SignalKind
    reason: str | None = Field(default=None, max_length=REASON_MAX)


@event_type("run.started", 1)
class RunStartedV1(EventPayload):
    event_name: ClassVar[str] = "run.started"
    schema_version: Literal[1] = 1
    run_id: UUID
    task_id: UUID | None
    project_id: UUID | None
    kind: RunKind
    status: Literal["running"] = "running"


@event_type("run.finished", 1)
class RunFinishedV1(EventPayload):
    event_name: ClassVar[str] = "run.finished"
    schema_version: Literal[1] = 1
    run_id: UUID
    task_id: UUID | None
    kind: RunKind
    status: TerminalStatus
    stop_reason: str | None = Field(default=None, max_length=REASON_MAX)
    duration_s: float | None = Field(
        default=None, ge=0
    )  # started to finished; None if never started


PROMPT_MAX: Final = 4000  # a question's prompt and an approval's description (plan)


@event_type("question.asked", 1)
class QuestionAskedV1(EventPayload):
    """A run asked the human (P2-05, FR-5.7): the question row, its review item and the
    task's move to Waiting on human are in the same transaction; `agents.start_human_wait`
    starts its `question_flow`."""

    event_name: ClassVar[str] = "question.asked"
    schema_version: Literal[1] = 1
    question_id: UUID
    run_id: UUID
    task_id: UUID | None
    prompt: str = Field(max_length=PROMPT_MAX)


@event_type("approval.requested", 1)
class ApprovalRequestedV1(EventPayload):
    """A run asked to take an action that needs a decision (P2-05, FR-5.6): `rule` is the
    policy's verdict. `opened` is true when the human was asked in the same transaction
    (review item, task Waiting on human); false while Decisions has yet to judge an action
    the policy does not name (`unknown_needs_decision`). `agents.start_human_wait` starts
    its `approval_flow`."""

    event_name: ClassVar[str] = "approval.requested"
    schema_version: Literal[1] = 1
    approval_id: UUID
    run_id: UUID
    task_id: UUID | None
    action_class: str = Field(max_length=200)
    rule: str = Field(max_length=60)
    opened: bool


PauseScope = Literal["workspace", "project"]


@event_type("agents.paused", 1)
class AgentsPausedV1(EventPayload):
    event_name: ClassVar[str] = "agents.paused"
    schema_version: Literal[1] = 1
    pause_id: UUID
    scope: PauseScope
    project_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=REASON_MAX)
    paused_at: datetime


@event_type("agents.resumed", 1)
class AgentsResumedV1(EventPayload):
    event_name: ClassVar[str] = "agents.resumed"
    schema_version: Literal[1] = 1
    pause_id: UUID
    scope: PauseScope
    project_id: UUID | None = None
    resumed_at: datetime
