"""focus DBOS workflows and steps (P2-15, FR-10.2, R-30; [DBOS communication]
(https://docs.dbos.dev/python/tutorials/workflow-communication)).

- `focus_plan(workspace_id, plan_id, day, start)`: the day's planned events (block starts,
  not-started checks, block ends, the end of working hours) fired in order, each when its
  instant comes (`api.fire_planned`). Workflow id `focus_plan:<ws>:<day>:<plan id>`; a newer
  plan of the day sends it `superseded` and it stops.
- `focus_session(workspace_id, session_id, started_at)`: one In progress task's check-ins
  (`api.check_in`) at the cadence and level in force, until its task leaves In progress
  (`end`). Workflow id `focus_session:<task id>:<started_at>`.

Both wait with `DBOS.recv_async` on topic `focus` (the durable sleep; R-30), at most
`WAIT_SLICE_S` at a time. They never read a clock: the time they act at comes from their
input (the start instant), from a message (`tick` and `wake` carry `now`, `end` and
`response` carry `at`) or from the wait itself (a wait that ran out reached its due
instant; a wait capped by the slice moved time on by the slice). So a test moves them with
the `focus-wake` tick (`testing.wake`), and `use(min_due_seconds=...)` proves the timeout
path on real time: every wait is cut to that and counts as reaching its due instant.
"""

import asyncio
import contextvars
from collections.abc import Callable, Coroutine
from datetime import date, datetime, timedelta
from typing import Any, Final
from uuid import UUID

from dbos import DBOS, SetWorkflowID
from dbos._error import DBOSNonExistentWorkflowError  # dbos 3.1.0: not re-exported

from tumnis.core import faults
from tumnis.core.limits import WAIT_SLICE_S
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound
from tumnis.modules.focus import api

FOCUS_QUEUE: Final = "focus"
TOPIC: Final = "focus"
KILLPOINT_WAITING: Final = "focus.session.waiting"
ACTIVE: Final = ["ENQUEUED", "PENDING"]

_min_due: list[float | None] = [None]


def use(*, min_due_seconds: float | None = None) -> None:
    """Test seam (R-30): cut every wait to at most `min_due_seconds` of real time; a cut
    wait that runs out counts as reaching its due instant. No argument: production."""
    _min_due[0] = min_due_seconds


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


def plan_workflow_id(workspace_id: UUID, day: date, plan_id: UUID) -> str:
    return f"{plan_workflow_prefix(workspace_id, day)}{plan_id}"


def plan_workflow_prefix(workspace_id: UUID, day: date) -> str:
    return f"focus_plan:{workspace_id}:{day.isoformat()}:"


# --- Steps ---------------------------------------------------------------------------------


@DBOS.step(name="focus.planned_events")
async def planned_events(workspace_id: str, plan_id: str, day: str) -> list[dict[str, Any]]:
    found = await api.planned_events(_ctx(workspace_id), UUID(plan_id), date.fromisoformat(day))
    return [e.model_dump(mode="json") for e in found]


@DBOS.step(name="focus.fire_planned")
async def fire_planned(
    workspace_id: str, plan_id: str, day: str, event: dict[str, Any], now: str
) -> None:
    await api.fire_planned(
        _ctx(workspace_id),
        UUID(plan_id),
        date.fromisoformat(day),
        api.PlannedOut.model_validate(event),
        datetime.fromisoformat(now),
    )


@DBOS.step(name="focus.session_due")
async def session_due(workspace_id: str, session_id: str, now: str) -> dict[str, Any]:
    """{"open": False} once the session ended; else when its next check-in is due."""
    try:
        due = await api.session_due(
            _ctx(workspace_id), UUID(session_id), datetime.fromisoformat(now)
        )
    except NotFound:
        return {"open": False, "due": None}
    return {"open": True, "due": None if due is None else due.isoformat()}


@DBOS.step(name="focus.check_in")
async def check_in(workspace_id: str, session_id: str, now: str) -> bool:
    return await api.check_in(_ctx(workspace_id), UUID(session_id), datetime.fromisoformat(now))


# --- Waiting -------------------------------------------------------------------------------


async def _wait(due: datetime | None, now: datetime) -> tuple[str | None, datetime]:
    """Wait on the topic until `due` (or one slice); the message's kind (None: the wait ran
    out) and the time the workflow has reached."""
    left = None if due is None else max((due - now).total_seconds(), 0.0)
    timeout = float(WAIT_SLICE_S) if left is None else min(left, float(WAIT_SLICE_S))
    short = _min_due[0]
    cut = short is not None and timeout > short
    if short is not None and cut:
        timeout = short
    message = await DBOS.recv_async(TOPIC, timeout_seconds=timeout)
    if message is None:
        reached = due is not None and (cut or (left is not None and left <= timeout))
        if due is not None and reached:
            return None, max(now, due)
        if cut:
            return None, now  # no check-in due at this level: time stands
        return None, now + timedelta(seconds=timeout)
    stamp = message.get("now") or message.get("at") if isinstance(message, dict) else None
    if isinstance(stamp, str):
        now = max(now, datetime.fromisoformat(stamp))
    kind = message.get("kind") if isinstance(message, dict) else None
    return (kind if isinstance(kind, str) else None), now


# --- Workflows -----------------------------------------------------------------------------


@DBOS.workflow(name="focus_plan")
async def focus_plan(workspace_id: str, plan_id: str, day: str, start: str) -> None:
    """Fires the plan's events in order, each once its instant is reached; events before
    `start` (the plan's publication) are skipped. Ends early on `superseded`."""
    now = begun = datetime.fromisoformat(start)
    for event in await planned_events(workspace_id, plan_id, day):
        at = datetime.fromisoformat(event["at"])
        if at < begun:
            continue
        while now < at:
            kind, now = await _wait(at, now)
            if kind == "superseded":
                return
        await fire_planned(workspace_id, plan_id, day, event, now.isoformat())


@DBOS.workflow(name="focus_session")
async def focus_session(workspace_id: str, session_id: str, started_at: str) -> None:
    """The session's check-ins until it ends: wait for the next one (a message wakes it
    early: an answer or a level change moves the next check-in), check in when due."""
    now = datetime.fromisoformat(started_at)
    while True:
        due = await session_due(workspace_id, session_id, now.isoformat())
        if not due["open"]:
            return
        at = None if due["due"] is None else datetime.fromisoformat(due["due"])
        if at is not None and now >= at:
            if not await check_in(workspace_id, session_id, now.isoformat()):
                return
            continue
        faults.killpoint(KILLPOINT_WAITING)  # parked before the wait (T-P2-15-11)
        kind, now = await _wait(at, now)
        if kind == "end":
            return


# --- Starting and telling the workflows (from subscribers) --------------------------------


async def _fresh(make: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Run outside the subscriber's DBOS step: DBOS refuses to start a workflow from one."""
    await asyncio.get_running_loop().create_task(make(), context=contextvars.Context())


async def start_plan(workspace_id: UUID, plan_id: UUID, day: date, start: datetime) -> None:
    """Start the plan's `focus_plan` (once: its id is the plan's) and tell the day's older
    plans' workflows they are superseded."""
    workflow_id = plan_workflow_id(workspace_id, day, plan_id)

    async def run() -> None:
        older = await DBOS.list_workflows_async(
            workflow_id_prefix=plan_workflow_prefix(workspace_id, day),
            status=ACTIVE,
            load_input=False,
            load_output=False,
        )
        for flow in older:
            if flow.workflow_id != workflow_id:
                await send(flow.workflow_id, {"kind": "superseded"}, f"superseded:{plan_id}")
        with SetWorkflowID(workflow_id):
            await DBOS.enqueue_workflow_async(
                FOCUS_QUEUE,
                focus_plan,
                str(workspace_id),
                str(plan_id),
                day.isoformat(),
                start.isoformat(),
            )

    await _fresh(run)


async def start_session(workspace_id: UUID, started: api.SessionStart) -> None:
    async def run() -> None:
        with SetWorkflowID(started.workflow_id):
            await DBOS.enqueue_workflow_async(
                FOCUS_QUEUE,
                focus_session,
                str(workspace_id),
                str(started.session_id),
                started.started_at.isoformat(),
            )

    await _fresh(run)


async def send(workflow_id: str, message: dict[str, Any], key: str) -> None:
    """Tell one focus workflow (once per `key`); a workflow that is gone hears nothing."""
    try:
        await DBOS.send_async(workflow_id, message, topic=TOPIC, idempotency_key=key)
    except DBOSNonExistentWorkflowError:
        return
