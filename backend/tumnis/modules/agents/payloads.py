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
"""

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


@event_type("run.finished", 1)
class RunFinishedV1(EventPayload):
    event_name: ClassVar[str] = "run.finished"
    schema_version: Literal[1] = 1
    run_id: UUID
    task_id: UUID | None
    kind: RunKind
    status: TerminalStatus
    stop_reason: str | None = Field(default=None, max_length=REASON_MAX)
