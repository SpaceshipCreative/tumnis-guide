"""decisions DBOS workflows and steps.

`label_task` (P1-07, FR-3.3, FR-4.1): the quick-add label, started by the
`decisions.label_on_create` and `decisions.label_on_title_change` subscribers (events.py).
It takes three steps:

1. `load_label_task`: the task and only the whitelisted context (`rules.label_inputs`:
   title, parent title, project name and goal, the workspace's reserved judgments).
   Nothing is asked once the user has chosen the label.
2. `decide_label`: `api.decide` for `quick_add_label` (cache, limiter, fallback, log).
3. `apply_label`: APPLY writes the label with its one-line reason (`tasks.set_ai_label`,
   source `jev`, or `fallback` for a vLLM answer), which never overwrites a label the user
   set meanwhile; REVIEW with a label keeps it as a suggestion with a
   `low_confidence_label` item (`tasks.set_label_suggestion`); REVIEW without one (Jev
   said `unknown`) asks the human through a `decision_unavailable` item. When no provider
   answered, `decide` has already queued that item.

Starting: the label must land within 1 s of the task's commit (FR-3.3), so it waits on
no queue. The subscribers are `direct` (core.events): the relay runs them as it reads the
outbox, and `start_label` starts the workflow at once, on the relay's event loop, keyed on
the event (`label:<event_id>`). This is the plan's fallback ("the relay starts
`label_task` directly with `DBOS.start_workflow`"); Jev's request rate is held by the
credential's limiter in `decide` (R-32). Should the relay fall back to a queued delivery,
the handler runs inside a DBOS step, and DBOS (3.1) starts no workflow from inside one
(`DBOSContext.create_start_workflow_child` asserts it), so `start_label` always starts
from a task with a fresh context.
"""

import asyncio
import contextlib
import contextvars
from typing import Any, Final, Literal
from uuid import UUID

from dbos import DBOS, SetWorkflowID

from tumnis.core.settings_store import get_setting
from tumnis.core.tenancy import WorkspaceContext, tenant_session, use_workspace
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound
from tumnis.modules.decisions import api
from tumnis.modules.decisions.adapters.port import ChoiceAnswer, NoulAnswer
from tumnis.modules.decisions.catalog import DecisionPoint
from tumnis.modules.decisions.payloads import DecisionUnavailablePayload
from tumnis.modules.decisions.rules import Route, label_inputs, label_reason
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks

LABELS: Final = frozenset(label.value for label in tasks.Label)

Outcome = Literal["skipped", "applied", "suggested", "review", "unanswered"]


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


@DBOS.step()
async def load_label_task(workspace_id: str, task_id: str) -> dict[str, Any] | None:
    """The label request's inputs and the task's project; None when the task is gone or
    its label is the user's."""
    ctx = _ctx(workspace_id)
    with use_workspace(ctx):
        async with tenant_session(ctx) as s:
            try:
                task = await tasks.get_task(s, UUID(task_id))
            except NotFound:
                return None
            if not tasks.may_auto_label(task.label_source):
                return None
            parent_title = None
            if task.parent_id is not None:
                with contextlib.suppress(NotFound):
                    parent_title = (await tasks.get_task(s, task.parent_id)).title
            project = await projects.get_project(s, task.project_id)
        triage = await get_setting(ctx, api.TRIAGE_SECTION, api.TriageSettings)
    reserved = [] if triage is None else triage.value.reserved_judgments
    inputs = label_inputs(
        task.model_dump(mode="json"),
        parent_title=parent_title,
        project={"name": project.name, "goal": project.goal},
        reserved_judgments=reserved,
    )
    return {"inputs": inputs, "project_id": str(task.project_id), "title": task.title}


@DBOS.step()
async def decide_label(
    workspace_id: str, task_id: str, inputs: dict[str, Any], project_id: str
) -> dict[str, Any]:
    with use_workspace(_ctx(workspace_id)):
        decision = await api.decide(
            DecisionPoint.QUICK_ADD_LABEL,
            inputs,
            subject=api.SubjectRef(type="task", id=UUID(task_id)),
            project_id=UUID(project_id),
        )
    return decision.model_dump(mode="json")


@DBOS.step()
async def apply_label(
    workspace_id: str, task_id: str, project_id: str, title: str, decided: dict[str, Any]
) -> Outcome:
    """Writes nothing for a title the task no longer has (`for_title`)."""
    d = api.Decision.model_validate(decided)
    if d.provider == "none":
        return "unanswered"  # decide queued the decision_unavailable item itself
    answer = d.answers.get("label")
    confidence = answer.confidence if isinstance(answer, ChoiceAnswer) else None
    companions = {qid: a.noul for qid, a in d.answers.items() if isinstance(a, NoulAnswer)}
    label = tasks.Label(d.value) if d.value in LABELS else None
    reason = None if label is None else label_reason(label.value, companions)
    async with tenant_session(_ctx(workspace_id)) as s:
        if d.route is Route.APPLY and label is not None and reason is not None:
            change = await tasks.set_ai_label(
                s,
                UUID(task_id),
                label=label,
                source="fallback" if d.provider == "vllm" else "jev",
                reason=reason,
                confidence=confidence,
                decision_id=d.decision_id,
                for_title=title,
            )
            return "skipped" if change is None else "applied"
        if label is not None:
            probabilities = answer.probabilities if isinstance(answer, ChoiceAnswer) else {}
            kept = await tasks.set_label_suggestion(
                s,
                UUID(task_id),
                suggestion=label,
                reason=reason,
                confidence=confidence,
                decision_id=d.decision_id,
                probabilities=probabilities,
                for_title=title,
            )
            return "suggested" if kept else "skipped"
        task = await tasks.get_task(s, UUID(task_id))
        if not tasks.may_auto_label(task.label_source):
            return "skipped"
        await tasks.add_review_item(
            "decision_unavailable",
            target=tasks.TargetRef(type="task", id=UUID(task_id)),
            project_id=UUID(project_id),
            payload=DecisionUnavailablePayload(decision_id=d.decision_id, point=d.point).model_dump(
                mode="json"
            ),
            dedupe_key=f"decision:{d.point.value}:{task_id}",
            session=s,
        )
        return "review"


@DBOS.workflow(name="decisions.label_task")
async def label_task(workspace_id: str, task_id: str) -> Outcome:
    """Label one task with Jev's answer; the steps read the task's current row."""
    loaded = await load_label_task(workspace_id, task_id)
    if loaded is None:
        return "skipped"
    decided = await decide_label(workspace_id, task_id, loaded["inputs"], loaded["project_id"])
    return await apply_label(workspace_id, task_id, loaded["project_id"], loaded["title"], decided)


async def _start(workspace_id: UUID, task_id: UUID, event_id: UUID) -> None:
    with SetWorkflowID(label_workflow_id(event_id)):
        await DBOS.start_workflow_async(label_task, str(workspace_id), str(task_id))


def label_workflow_id(event_id: UUID) -> str:
    return f"label:{event_id}"


async def start_label(workspace_id: UUID, task_id: UUID, *, event_id: UUID) -> None:
    """Start `label_task` for the task, keyed on the event: a redelivered event finds the
    workflow it started (DBOS runs a workflow ID once). The workflow's first step skips a
    task that is gone or whose label the user chose."""
    # A fresh context: DBOS refuses to start a workflow from inside a step, which is where
    # the handler runs when the relay falls back to a queued delivery. Shielded: if the
    # relay stops waiting (its direct-handler timeout), the start still completes, and
    # the queued delivery's own start finds this workflow.
    await asyncio.shield(
        asyncio.create_task(_start(workspace_id, task_id, event_id), context=contextvars.Context())
    )
