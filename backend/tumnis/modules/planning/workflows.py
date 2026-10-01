"""planning DBOS workflows and steps (P1-11, FR-4.3).

- `planner_tick(scheduled_time, context)`: scheduled `*/5 * * * *` (`planner-tick`, UTC),
  so a DST change never shifts a plan: it asks `is_plan_due` per workspace in the
  workspace's own zone and enqueues one `build_plan` on the maintenance queue for each
  workspace whose morning plan is due. The workflow id `build_plan:<ws>:<day>:morning` is
  the idempotency key (the plan's deduplication id; DBOS 3.1.0 refuses a deduplication id
  on a partitioned queue, and this codebase keys every workflow by its id), so two ticks
  in the same window enqueue it once. A plan missed while the server was down is built on
  the first tick after recovery the same day.
- `build_plan(workspace_id, day, trigger, now)`: gathers the day (`gather_step`), asks the
  master profile when it is ready (a child `run_skill`, run id derived from this
  workflow's id so a replay dispatches nothing twice), checks the reply whole
  (`check_picks`; any violation rejects it), places Human and Hybrid tasks into free
  blocks (`assign_blocks`, or the due-date `fallback_plan` with a notice when the master is
  unreachable or its reply invalid), validates the result as a guard, and publishes
  (`publish_step`: one transaction, `plan.published` once). `now` is the build's clock:
  the tick's scheduled time for the morning plan, the request's time for a Re-plan.

Nothing else enqueues `build_plan`: nothing re-plans on its own during the day.
"""

import logging
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID, uuid5

from dbos import DBOS, SetWorkflowID
from pydantic import ValidationError

from tumnis.core import audit, db, faults
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api as agents
from tumnis.modules.planning import api
from tumnis.modules.planning.rules import (
    PlanContext,
    PlanPick,
    Unplaceable,
    assign_blocks,
    check_picks,
    fallback_plan,
    validate_plan,
)

_log = logging.getLogger(__name__)

PLANNER_TICK_NAME: Final = "planner-tick"
PLANNER_TICK_SCHEDULE: Final = "*/5 * * * *"  # every 5 minutes, UTC (architecture: workflow map)
_PLAN_RUNS: Final = UUID("2b7e4c1a-9d3f-5e6a-8b1c-0d2e3f4a5b6c")  # uuid5 namespace of run ids
# Run outcomes that mean the master could not be reached (agent offline), not a bad reply.
_UNREACHABLE: Final = frozenset({"timed_out", "runner_lost", "cancelled"})


def _ctx(workspace_id: UUID | str) -> WorkspaceContext:
    return WorkspaceContext(UUID(str(workspace_id)), SYSTEM_ACTOR)


# --- planner_tick -------------------------------------------------------------------------


@DBOS.step()
async def due_workspaces_step(scheduled_time: datetime) -> list[tuple[str, str]]:
    """(workspace id, local day) of each workspace whose morning plan is due."""
    async with db.app_sessionmaker()() as s, s.begin():
        workspace_ids = list(await audit.workspace_ids(s))
    due: list[tuple[str, str]] = []
    for workspace_id in workspace_ids:
        try:
            day = await api.due_plan_day(_ctx(workspace_id), scheduled_time)
        except Exception:  # one workspace's failure never holds up the others
            _log.exception("planner tick: workspace %s", workspace_id)
            continue
        if day is not None:
            due.append((str(workspace_id), day.isoformat()))
    return due


@DBOS.workflow(name="planner_tick")
async def planner_tick(scheduled_time: datetime, context: Any) -> int:
    """Scheduled every 5 minutes (DBOS passes the scheduled time and the schedule's
    context); returns how many builds it enqueued (a build already enqueued counts)."""
    started = 0
    for workspace_id, day in await due_workspaces_step(scheduled_time):
        local_day = date.fromisoformat(day)
        with SetWorkflowID(api.plan_workflow_id(UUID(workspace_id), local_day, "morning")):
            await DBOS.enqueue_workflow_async(
                api.MAINTENANCE_QUEUE, build_plan, workspace_id, day, "morning", scheduled_time
            )
        started += 1
    return started


def schedules() -> list[Any]:
    """This module's DBOS schedules, applied by the worker after launch."""
    return [
        {
            "schedule_name": PLANNER_TICK_NAME,
            "workflow_fn": planner_tick,
            "schedule": PLANNER_TICK_SCHEDULE,
            "queue_name": api.MAINTENANCE_QUEUE,
        }
    ]


# --- build_plan ---------------------------------------------------------------------------


@DBOS.step()
async def gather_step(workspace_id: str, day: str, trigger: str, now: str) -> dict[str, Any]:
    gathered = await api.gather_plan(
        _ctx(workspace_id),
        date.fromisoformat(day),
        trigger=trigger,
        now=datetime.fromisoformat(now),
    )
    return gathered.model_dump(mode="json")


@DBOS.step()
async def master_step(workspace_id: str) -> dict[str, Any]:
    return (await agents.master_agent(ctx=_ctx(workspace_id))).model_dump(mode="json")


@DBOS.step()
async def settings_step(workspace_id: str) -> dict[str, Any]:
    return (await api.planning_settings(_ctx(workspace_id))).model_dump(mode="json")


def _loose_picks(output: dict[str, Any] | None) -> list[PlanPick] | None:
    """The picks of a reply read loosely (a reply the schema refuses still names its
    violations: six picks, an empty reason); None when it has no list of picks."""
    if not isinstance(output, dict) or not isinstance(output.get("picks"), list):
        return None
    picks: list[PlanPick] = []
    for raw in output["picks"]:
        if not isinstance(raw, dict):
            return None
        try:
            picks.append(
                PlanPick(task_id=UUID(str(raw.get("task_id"))), reason=str(raw.get("reason") or ""))
            )
        except ValueError:
            return None
    return picks


def check_reply(outcome: agents.RunOutcome, ctx: PlanContext) -> tuple[list[PlanPick] | None, str]:
    """The master's picks when its reply is valid (None otherwise) and why it was refused:
    the violation codes (`check_picks`), `no_json` without output, or the schema's
    refusal. A reply is rejected whole, never repaired."""
    picks = _loose_picks(outcome.output_json)
    if picks is None:
        return None, "no_json" if outcome.output_json is None else "invalid_output"
    codes = list(dict.fromkeys(v.code for v in check_picks(picks, ctx)))
    if codes:
        return None, ",".join(codes)
    if outcome.status != "succeeded":
        return None, outcome.error or "invalid_output"
    try:
        agents.PlanningResult.model_validate(outcome.output_json)
    except ValidationError:
        return None, "invalid_output"
    return picks, ""


@DBOS.step()
async def publish_step(workspace_id: str, draft: dict[str, Any]) -> str:
    plan_id = await api.publish_plan(_ctx(workspace_id), api.PlanDraft.model_validate(draft))
    faults.killpoint("planning.publish_step")  # the plan has committed
    return str(plan_id)


def _place(picks: list[PlanPick] | None, ctx: PlanContext) -> tuple[list[Any], list[Unplaceable]]:
    items, issues = assign_blocks(picks, ctx) if picks is not None else fallback_plan(ctx)
    violations = validate_plan(items, ctx)
    if violations:  # a placement bug: never publish a plan the rules refuse
        raise RuntimeError(f"placed plan does not validate: {[v.code for v in violations]}")
    return items, issues


@DBOS.workflow(name="build_plan")
async def build_plan(
    workspace_id: UUID | str, day: date | str, trigger: str, now: datetime | str
) -> UUID:
    """Builds and publishes the day's plan; returns its id (see the module docstring)."""
    ws = str(workspace_id)
    local_day = day if isinstance(day, date) else date.fromisoformat(day)
    at = now if isinstance(now, datetime) else datetime.fromisoformat(now)
    gathered = api.GatheredPlan.model_validate(
        await gather_step(ws, local_day.isoformat(), trigger, at.isoformat())
    )
    ctx = gathered.context
    master = agents.MasterAgentOut.model_validate(await master_step(ws))
    picks: list[PlanPick] | None = None
    source: api.PlanSource = "fallback"
    notice: api.PlanNotice | None = "agent_offline"
    reason: str | None = None
    run_id: UUID | None = None
    if master.availability == "ready" and master.profile_id is not None:
        settings = api.PlanningSettings.model_validate(await settings_step(ws))
        run_id = uuid5(_PLAN_RUNS, DBOS.workflow_id or f"{ws}:{local_day}")
        packet = agents.plan_packet(
            run_id=run_id,
            profile_id=master.profile_id,
            request=agents.PlanningRequest.model_validate(gathered.request),
            timeout_s=settings.run_timeout_s,
            correlation_id=f"plan:{local_day.isoformat()}",
        )
        outcome = await agents.run_plan(UUID(ws), packet)
        unreachable = outcome.status in _UNREACHABLE or (outcome.error or "").startswith(
            "agent_unavailable"
        )
        if unreachable:
            reason = outcome.error or outcome.status
            if (outcome.error or "").startswith("agent_unavailable"):
                run_id = None  # refused before any run was written
        else:
            picks, why = check_reply(outcome, ctx)
            if picks is not None:
                source, notice = "master", None
            else:
                notice, reason = "invalid_plan", why
                _log.warning("plan reply rejected for %s %s: %s", ws, local_day, why)
    items, issues = _place(picks, ctx)
    draft = api.PlanDraft(
        plan_id=api.plan_id_for(DBOS.workflow_id or f"{ws}:{local_day}:{trigger}"),
        day=local_day,
        trigger="replan" if trigger == "replan" else "morning",
        source=source,
        notice=notice,
        fallback_reason=reason,
        master_run_id=run_id,
        profile_version=master.profile_version,
        built_at=at,
        items=items,
        issues=issues,
    )
    return UUID(await publish_step(ws, draft.model_dump(mode="json")))
