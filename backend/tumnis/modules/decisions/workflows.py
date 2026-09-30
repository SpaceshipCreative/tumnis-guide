"""decisions DBOS workflows and steps.

`label_task` (P1-07, FR-3.3, FR-4.1): the quick-add label. It runs on the `decisions`
queue, enqueued by the `decisions.label_on_create` and `decisions.label_on_title_change`
subscribers (events.py), and takes three steps:

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

Enqueueing: the subscriber runs inside the delivery workflow's handler step, and DBOS
(3.1) starts no workflow from inside a step (`DBOSContext.create_start_workflow_child`
asserts it is not in one). `enqueue_label` therefore enqueues from a task with a fresh
context (no DBOS context), keyed on the event (`label:<event_id>`: a redelivered event
finds the same workflow) and deduplicated per task version while one is queued.
"""

import asyncio
import contextlib
import contextvars
from typing import Any, Final, Literal
from uuid import UUID

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID
from dbos._error import DBOSQueueDeduplicatedError  # dbos 3.1.0: not re-exported

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

DECISIONS_QUEUE: Final = "decisions"
# DBOS polls a queue on this interval (default 1 s); the label must land within 1 s of the
# task's commit (FR-3.3), so the queue hop gets at most 100 ms (plan default).
DECISIONS_QUEUE_POLL_S: Final = 0.1
DECISIONS_PER_MINUTE: Final = 1_200  # Jev's request limit per 60 s (FR-11.9, R-32)
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
    return {"inputs": inputs, "project_id": str(task.project_id)}


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
    workspace_id: str, task_id: str, project_id: str, decided: dict[str, Any]
) -> Outcome:
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
async def label_task(workspace_id: str, task_id: str, task_version: int) -> Outcome:
    """Label one task (at `task_version`, when it was enqueued) with Jev's answer."""
    del task_version  # part of the deduplication id; the steps read the current row
    loaded = await load_label_task(workspace_id, task_id)
    if loaded is None:
        return "skipped"
    decided = await decide_label(workspace_id, task_id, loaded["inputs"], loaded["project_id"])
    return await apply_label(workspace_id, task_id, loaded["project_id"], decided)


async def _enqueue(workspace_id: UUID, task_id: UUID, version: int, event_id: UUID) -> None:
    with (
        SetWorkflowID(f"label:{event_id}"),
        SetEnqueueOptions(deduplication_id=f"label:{task_id}:{version}"),
        contextlib.suppress(DBOSQueueDeduplicatedError),  # this version is queued already
    ):
        await DBOS.enqueue_workflow_async(
            DECISIONS_QUEUE, label_task, str(workspace_id), str(task_id), version
        )


async def enqueue_label(workspace_id: UUID, task_id: UUID, *, event_id: UUID) -> bool:
    """Queue `label_task` for the task unless it is gone or the user chose its label;
    idempotent per event. Returns whether it was queued."""
    async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
        try:
            task = await tasks.get_task(s, task_id)
        except NotFound:
            return False
    if not tasks.may_auto_label(task.label_source):
        return False
    # A fresh context: DBOS refuses to start a workflow from inside a step (the handler's).
    await asyncio.create_task(
        _enqueue(workspace_id, task_id, task.version, event_id), context=contextvars.Context()
    )
    return True
