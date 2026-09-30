"""Enrichment by the project agent (P1-08, FR-4.4, FR-4.6, UX 5, UX 9).

A task missing its first action or acceptance criteria, or a Human or Hybrid task missing
its estimate, gets them from its project's Hermes profile through `enrich_task`: a
placeholder first action from the Generation slot shows first, the agent's values replace
it, a user's edit made meanwhile wins, the result is undoable, and an offline project agent
degrades only its own project.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import TYPE_CHECKING, Any, Literal

import pytest

from tests._pg import APP
from tumnis.modules.agents.tests.integration._enrich import (
    PLACEHOLDER,
    LiveListener,
    agent_project,
    enrichment_settings,
    execute,
    new_task,
    recorded,
    relay,
    rows,
    task_row,
    until,
    user_ctx,
)

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import AppFactory, WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

ACME, BETA = "acme-site", "beta-app"
SKILL = "enrich"


def _subscribers() -> None:
    import tumnis.modules.agents.events  # noqa: F401, PLC0415  # registers the subscribers


def _status(db: DbUrls, task_id: uuid.UUID, *statuses: str) -> Any:
    def check() -> bool:
        return task_row(db, task_id)["enrichment_status"] in statuses

    return check


def _criteria_lines(text: str | None) -> list[str]:
    return [line[2:] for line in (text or "").splitlines() if line.startswith("- ")]


def _outlier_items(db: DbUrls, task_id: uuid.UUID) -> list[dict[str, Any]]:
    return rows(
        db,
        "SELECT payload FROM review_items WHERE kind = 'estimate_outlier' AND target_id = %s",
        task_id,
    )


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
async def test_missing_first_action_and_criteria_are_filled(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-02
    A Human task created without first action, criteria or estimate: `task.created` starts
    the enrichment, and the task gets all three from the fake runner's `enrich` result; the
    run is recorded with `kind = "enrich"`.
    """
    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    result = recorded("enrich_ok_human")
    runner.script(ACME, SKILL, result)

    with enrichment_settings(clock):
        task = await new_task(workspace, clock, project_id, "Send the March invoice", label="human")
        await relay()
        assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"

    row = task_row(db, task.id)
    assert row["enrichment_status"] == "done"
    assert row["first_action"] == result["first_action"]
    assert row["first_action_source"] == "agent"
    assert _criteria_lines(row["acceptance_criteria"]) == result["acceptance_criteria"]
    assert row["estimate_minutes"] == result["estimate_minutes"]
    runs = rows(db, "SELECT kind, status, task_id FROM runs")
    assert runs == [{"kind": "enrich", "status": "succeeded", "task_id": task.id}]
    assert len(runner.runs()) == 1


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
async def test_ai_task_never_gets_estimate(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-03
    An AI task: the request's `missing` names the first action and criteria, never the
    estimate; a result carrying an estimate breaks `enrichment_errors`, so nothing of it is
    applied and the enrichment ends `failed`.
    """
    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    runner.script(ACME, SKILL, recorded("enrich_estimate_for_ai"))

    with enrichment_settings(clock):
        task = await new_task(workspace, clock, project_id, "Export the sign-ups", label="ai")
        await relay()
        assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"

    [run] = runner.runs()
    body = run.packet["body"]
    assert body["missing"] == ["first_action", "acceptance_criteria"]
    assert body["task"]["label"] == "ai"
    row = task_row(db, task.id)
    assert row["enrichment_status"] == "failed"
    assert row["first_action_source"] in {None, "placeholder"}
    assert row["acceptance_criteria"] is None
    assert row["estimate_minutes"] is None


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
async def test_hybrid_gets_human_portion_estimate(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-04
    A Hybrid task gets the estimate of its human portion, and the split is written under
    the fixed headings `AI part` and `Your part` after the acceptance criteria (the task has
    no description column; the criteria are the task's long text the packet carries).
    """
    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    result = recorded("enrich_ok_hybrid")
    runner.script(ACME, SKILL, result)

    with enrichment_settings(clock):
        task = await new_task(
            workspace, clock, project_id, "Refresh the help pages", label="hybrid"
        )
        await relay()
        assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"

    row = task_row(db, task.id)
    assert row["enrichment_status"] == "done"
    assert row["estimate_minutes"] == result["estimate_minutes"]
    text = row["acceptance_criteria"]
    assert _criteria_lines(text) == result["acceptance_criteria"]
    assert f"AI part: {result['hybrid_split']['ai_portion']}" in text
    assert f"Your part: {result['hybrid_split']['human_portion']}" in text


@pytest.mark.req("FR-4.6", "UX 5")
@pytest.mark.wp("P1-08")
async def test_placeholder_first_then_replaced(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-05
    The Generation slot's placeholder is written with `first_action_source = "placeholder"`
    before the agent answers (the runner answers after 1.5 s), then replaced by the agent's
    first action; both writes reach `/ws` (the live channel carries the task each time).
    """
    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    result = recorded("enrich_ok_human")
    runner.script(ACME, SKILL, result, delay_ms=1500)

    with enrichment_settings(clock), LiveListener(db) as live:
        task = await new_task(workspace, clock, project_id, "Send the March invoice", label="human")
        created = live.of("task", task.id)
        await relay()

        def placeholder_shown() -> bool:
            row = task_row(db, task.id)
            return bool(row["first_action_source"] == "placeholder")

        assert await until(placeholder_shown), "no placeholder"
        row = task_row(db, task.id)
        assert row["first_action"] == PLACEHOLDER
        assert rows(db, "SELECT count(*) AS n FROM run_events WHERE kind = 'result'") == [{"n": 0}]
        placeholder_heard = await until(lambda: live.of("task", task.id) > created)
        assert placeholder_heard

        assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"
        after_placeholder = live.of("task", task.id)
        await until(lambda: live.of("task", task.id) > after_placeholder, timeout_s=2)

    row = task_row(db, task.id)
    assert row["first_action"] == result["first_action"]
    assert row["first_action_source"] == "agent"
    assert live.of("task", task.id) >= created + 2


@pytest.mark.req("FR-4.6", "UX 5")
@pytest.mark.wp("P1-08")
async def test_generation_timeout_leaves_first_action_pending(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-06
    The Generation fake answers after its budget (R-30: 300 ms here, 2 s by default): no
    placeholder is written, the enrichment is `running` with the first action still
    pending, and the agent's value still arrives.
    """
    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    result = recorded("enrich_ok_human")
    runner.script(ACME, SKILL, result, delay_ms=2000)

    with enrichment_settings(clock, generation_delay_ms=3000, placeholder_timeout_ms=300):
        task = await new_task(workspace, clock, project_id, "Send the March invoice", label="human")
        await relay()
        assert await until(runner.runs), "the run was never dispatched"
        row = task_row(db, task.id)
        assert row["enrichment_status"] == "running"
        assert row["first_action"] is None
        assert row["first_action_source"] is None
        assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"

    row = task_row(db, task.id)
    assert row["enrichment_status"] == "done"
    assert row["first_action"] == result["first_action"]
    assert row["first_action_source"] == "agent"


@pytest.mark.req("FR-4.6")
@pytest.mark.wp("P1-08")
async def test_one_project_offline_other_completes(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-07
    `acme-site`'s runner has sent no heartbeat for 46 s on the fixed clock (offline), while
    `beta-app`'s runner has just beaten: the Acme task keeps its Jev label, gets no first
    action from an agent and ends `agent_offline`; the Beta task ends `done` with every
    field from its agent.
    """
    from tests._labels import label_answers, reset_label_fakes, use_label_fakes  # noqa: PLC0415
    from tumnis.core.adapters.registry import resolve  # noqa: PLC0415

    _subscribers()
    acme_runner = fake_runner(profiles=[ACME], name="acme-host")
    beta_runner = fake_runner(profiles=[BETA], name="beta-host")
    acme = agent_project(fake_runner, acme_runner, ACME, db)
    beta = agent_project(fake_runner, beta_runner, BETA, db)
    result = recorded("enrich_ok_human")
    acme_runner.script(ACME, SKILL, result)
    beta_runner.script(BETA, SKILL, result)
    clock.advance(seconds=46)
    beta_runner.heartbeat()
    await until(lambda: rows(db, "SELECT 1 FROM runners WHERE last_heartbeat_at = %s", clock.now()))
    jev = resolve("decisions.jev", "fake")
    jev.script("quick_add_label", label_answers("human", 0.93))
    use_label_fakes(jev)
    try:
        with enrichment_settings(clock, label_wait_s=10):
            a = await new_task(workspace, clock, acme, "Send the March invoice")
            b = await new_task(workspace, clock, beta, "Fix the sign-up form")
            await relay()
            assert await until(_status(db, a.id, "agent_offline", "done", "failed"))
            assert await until(_status(db, b.id, "agent_offline", "done", "failed"))
    finally:
        reset_label_fakes()

    acme_row, beta_row = task_row(db, a.id), task_row(db, b.id)
    assert acme_row["enrichment_status"] == "agent_offline"
    assert acme_row["label"] == "human"
    assert acme_row["label_source"] == "jev"
    assert acme_row["first_action_source"] in {None, "placeholder"}
    assert acme_row["estimate_minutes"] is None
    assert acme_runner.runs() == []

    assert beta_row["enrichment_status"] == "done"
    assert beta_row["first_action"] == result["first_action"]
    assert beta_row["first_action_source"] == "agent"
    assert _criteria_lines(beta_row["acceptance_criteria"]) == result["acceptance_criteria"]
    assert beta_row["estimate_minutes"] == result["estimate_minutes"]
    assert rows(db, "SELECT event_name FROM dead_letters") == []


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
async def test_request_carries_brief_and_estimate_history(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-08
    The packet the fake runner receives carries the project's brief and its 10 most
    recently finished Human or Hybrid tasks with both an estimate and an actual time,
    newest first (12 exist; an AI task and an unfinished one are left out).
    """
    from datetime import timedelta  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    runner.script(ACME, SKILL, recorded("enrich_ok_human"))
    brief = "Acme site: a five-page marketing site. Invoices go out on the first Monday."
    async with tenant_session(user_ctx(workspace)) as s:
        await knowledge.put_text_document(s, project_id, title="Brief", body_md=brief, role="brief")
    finished = []
    for n in range(12):
        done = await new_task(workspace, clock, project_id, f"Past task {n}", label="human")
        execute(
            db,
            "UPDATE tasks SET status = 'done', estimate_minutes = %s, actual_minutes = %s,"
            " completed_at = %s, first_action = 'x', acceptance_criteria = '- x' WHERE id = %s",
            10 + n,
            20 + n,
            clock.now() - timedelta(days=12 - n),
            done.id,
        )
        finished.append((f"Past task {n}", 10 + n, 20 + n))
    ai = await new_task(workspace, clock, project_id, "Past AI task", label="ai")
    execute(
        db,
        "UPDATE tasks SET status = 'done', completed_at = %s, actual_minutes = 5,"
        " first_action = 'x', acceptance_criteria = '- x' WHERE id = %s",
        clock.now(),
        ai.id,
    )
    execute(db, "UPDATE outbox SET sent_at = now()")  # only the next task is enriched

    with enrichment_settings(clock):
        task = await new_task(workspace, clock, project_id, "Send the March invoice", label="human")
        await relay()
        assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"

    [run] = runner.runs()
    body = run.packet["body"]
    assert body["brief"] == brief
    history = [
        (h["title"], h["estimate_minutes"], h["actual_minutes"]) for h in body["estimate_history"]
    ]
    assert history == list(reversed(finished))[:10]
    assert all(h["label"] == "human" for h in body["estimate_history"])


@pytest.mark.req("FR-4.4", "FR-11.4")
@pytest.mark.wp("P1-08")
async def test_plausibility_flags_outlier(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-09
    The applied estimate goes to Jev's `estimate_plausibility` Score: `Far too low` with
    confidence 0.9 queues one `estimate_outlier` item (flag `too_low`) and keeps the
    estimate; a plausible score queues none; with both decision providers down, none.
    """
    from tests._labels import reset_label_fakes, use_label_fakes  # noqa: PLC0415
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415
    from tumnis.core.adapters.registry import resolve  # noqa: PLC0415

    def score(level: int, confidence: float) -> dict[str, Any]:
        rest = (1 - confidence) / 4
        probabilities = {str(n): confidence if n == level else rest for n in range(5)}
        value = sum(n * p for n, p in enumerate(probabilities.values()))
        return {
            "plausibility": {
                "type": "score",
                "score": value,
                "confidence": confidence,
                "probabilities": probabilities,
            }
        }

    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    result = recorded("enrich_ok_human")
    runner.script(ACME, SKILL, result)
    jev, vllm = resolve("decisions.jev", "fake"), resolve("decisions.vllm", "fake")
    use_label_fakes(jev, vllm)
    try:
        with enrichment_settings(clock):
            jev.script("estimate_plausibility", score(0, 0.9))
            low = await new_task(workspace, clock, project_id, "Write the proposal", label="human")
            await relay()
            assert await until(_status(db, low.id, "done", "failed"))

            jev.script("estimate_plausibility", score(2, 0.9))
            fine = await new_task(workspace, clock, project_id, "Book the venue", label="human")
            await relay()
            assert await until(_status(db, fine.id, "done", "failed"))

            down = AdapterUnavailable("decisions.jev", "ask", "down (P1-08)")
            jev.script("estimate_plausibility", fail=down)
            vllm.script("estimate_plausibility", fail=down)
            unanswered = await new_task(
                workspace, clock, project_id, "Call the printer", label="human"
            )
            await relay()
            assert await until(_status(db, unanswered.id, "done", "failed"))
    finally:
        reset_label_fakes()

    [item] = _outlier_items(db, low.id)
    assert item["payload"]["flag"] == "too_low"
    assert item["payload"]["estimate_minutes"] == result["estimate_minutes"]
    assert task_row(db, low.id)["estimate_minutes"] == result["estimate_minutes"]
    assert _outlier_items(db, fine.id) == []
    assert _outlier_items(db, unanswered.id) == []
    for task in (low, fine, unanswered):
        assert task_row(db, task.id)["enrichment_status"] == "done"


@pytest.mark.req("UX 9")
@pytest.mark.wp("P1-08")
async def test_user_edit_during_run_wins(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-10
    The user writes a first action while the run is in flight (the runner's answer waits on
    a gate the test opens after the user's PATCH commits): the agent's first action is not
    applied; its criteria and estimate still are.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    result = recorded("enrich_ok_human")
    gate = threading.Event()
    runner.script(ACME, SKILL, result, gate=gate)
    mine = "Ring Dana before writing anything"

    with enrichment_settings(clock):
        task = await new_task(workspace, clock, project_id, "Send the March invoice", label="human")
        await relay()
        assert await until(runner.runs), "the run was never dispatched"
        ctx = user_ctx(workspace)
        async with tenant_session(ctx) as s:
            now = await tasks.get_task(s, task.id)
            await tasks.update_task(
                s,
                ctx.actor,
                task.id,
                tasks.TaskPatch(first_action=mine, version=now.version),
                now.version,
                now=clock.now(),
            )
        gate.set()
        assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"

    row = task_row(db, task.id)
    assert row["enrichment_status"] == "done"
    assert row["first_action"] == mine
    assert row["first_action_source"] is None
    assert _criteria_lines(row["acceptance_criteria"]) == result["acceptance_criteria"]
    assert row["estimate_minutes"] == result["estimate_minutes"]


@pytest.mark.req("UX 9")
@pytest.mark.wp("P1-08")
async def test_enrichment_undo_restores_previous_values(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P1-08-11
    `GET /v1/tasks/{id}` names the enrichment's change; `POST /v1/tasks/{id}/undo
    {change_id, version}` with it puts back the placeholder first action, empty criteria
    and no estimate from the `task_changes` row, for the person, once.
    """
    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    runner.script(ACME, SKILL, recorded("enrich_ok_human"), delay_ms=500)

    with enrichment_settings(clock):
        task = await new_task(workspace, clock, project_id, "Send the March invoice", label="human")
        await relay()
        assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"

    got = await session_client.get(f"/v1/tasks/{task.id}")
    assert got.status_code == 200
    enriched = got.json()
    assert enriched["first_action_source"] == "agent"
    assert enriched["enrichment_status"] == "done"
    assert enriched["change_id"] is not None
    undone = await session_client.post(
        f"/v1/tasks/{task.id}/undo",
        json={"change_id": enriched["change_id"], "version": enriched["version"]},
    )
    assert undone.status_code == 200, undone.text
    body = undone.json()
    assert body["first_action"] == PLACEHOLDER
    assert body["first_action_source"] == "placeholder"
    assert body["acceptance_criteria"] is None
    assert body["estimate_minutes"] is None
    row = task_row(db, task.id)
    assert (row["first_action"], row["first_action_source"]) == (PLACEHOLDER, "placeholder")
    assert (row["acceptance_criteria"], row["estimate_minutes"]) == (None, None)
    again = await session_client.post(
        f"/v1/tasks/{task.id}/undo",
        json={"change_id": enriched["change_id"], "version": body["version"]},
    )
    assert again.status_code == 409


@pytest.mark.req("FR-4.1")
@pytest.mark.wp("P1-08")
async def test_agent_label_revision_respects_human_choice(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-12
    The agent revises the label to Human: the revision applies over a `jev` or `fallback`
    label and over a pending (NULL) one, setting `label_source = "agent"`; a label the user
    chose stays as it is.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import workflows  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    revised = {
        **recorded("enrich_ok_human"),
        "label_revision": {"label": "human", "reason": "It needs a phone call"},
    }
    runner.script(ACME, SKILL, revised)

    with enrichment_settings(clock, label_wait_s=1):
        by_jev = await new_task(workspace, clock, project_id, "Chase the invoice")
        by_fallback = await new_task(workspace, clock, project_id, "Chase the deposit")
        pending = await new_task(workspace, clock, project_id, "Chase the contract")
        by_user = await new_task(workspace, clock, project_id, "Chase the logo", label="ai")
        async with tenant_session(user_ctx(workspace)) as s:
            ai_labelled: list[tuple[Any, Literal["jev", "fallback"]]] = [
                (by_jev, "jev"),
                (by_fallback, "fallback"),
            ]
            for task, source in ai_labelled:
                await tasks.set_ai_label(
                    s,
                    task.id,
                    label=tasks.Label.AI,
                    source=source,
                    reason="Looks automatable",
                    confidence=0.9,
                    decision_id=uuid.uuid4(),
                )
        for task in (by_jev, by_fallback, pending, by_user):
            await workflows.start_enrichment(workspace.id, task.id, project_id, key="test")
        for task in (by_jev, by_fallback, pending, by_user):
            assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"

    for task in (by_jev, by_fallback, pending):
        row = task_row(db, task.id)
        assert (row["label"], row["label_source"]) == ("human", "agent"), task.title
        assert row["label_reason"] == "It needs a phone call"
    row = task_row(db, by_user.id)
    assert (row["label"], row["label_source"]) == ("ai", "user")
    assert row["estimate_minutes"] is None


@pytest.mark.req("FR-4.6")
@pytest.mark.wp("P1-08")
async def test_invalid_json_fails_cleanly(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-13
    The runner replies without JSON (`no_json`): the enrichment ends `failed`, the
    placeholder stays for the user to replace, the run row holds the error, and nothing
    reaches the dead-letter table.
    """
    _subscribers()
    runner = fake_runner(profiles=[ACME])
    project_id = agent_project(fake_runner, runner, ACME, db)
    runner.script(ACME, SKILL, None)

    with enrichment_settings(clock):
        task = await new_task(workspace, clock, project_id, "Send the March invoice", label="human")
        await relay()
        assert await until(_status(db, task.id, "done", "failed")), "no enrichment in time"
        await asyncio.sleep(0.5)
        await relay()

    row = task_row(db, task.id)
    assert row["enrichment_status"] == "failed"
    assert (row["first_action"], row["first_action_source"]) == (PLACEHOLDER, "placeholder")
    assert row["acceptance_criteria"] is None
    [run] = rows(db, "SELECT status, error FROM runs WHERE task_id = %s", task.id)
    assert run["status"] == "failed"
    assert "no_json" in (run["error"] or "")
    assert rows(db, "SELECT event_name FROM dead_letters") == []


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
@pytest.mark.slow
async def test_killed_worker_does_not_double_run(
    worker_killer: WorkerKillerFactory,
    app_factory: AppFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-08-14
    A worker killed at `agents.dispatch_step` right after the `run` mailbox row committed:
    the restarted worker resumes the enrichment, the runner gets one `run` message and the
    result is applied once.
    """
    from tests.fakes.fake_runner import (  # noqa: PLC0415
        FakeRunner,
        create_runner,
        make_test_client,
        register_profile,
    )
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    killer = worker_killer("agents.dispatch_step", events=0)
    app = app_factory(dbos_system_database_url=killer.sys_db.url(APP))
    app.state.clock = SystemClock()  # the worker judges heartbeats on the real clock
    runner_id, token = create_runner(workspace, clock, "homelab-hermes")
    profile_id = register_profile(workspace, clock, ACME, runner_id=runner_id)
    execute(db, "UPDATE agent_profiles SET status = 'ready' WHERE id = %s", profile_id)
    [profile] = rows(db, "SELECT project_id FROM agent_profiles WHERE id = %s", profile_id)
    result = recorded("enrich_ok_human")

    def mailbox() -> int:
        [row] = rows(
            db,
            "SELECT count(*) AS n FROM runner_messages WHERE direction = 'out' AND type = 'run'",
        )
        return int(row["n"])

    with make_test_client(app) as http:
        runner = FakeRunner(
            http, token, runner_id, name="homelab-hermes", profiles=[ACME], clock=clock
        )
        runner.script(ACME, SKILL, result)
        runner.connect()
        try:
            task = await new_task(
                workspace, clock, profile["project_id"], "Send the March invoice", label="human"
            )
            assert await killer.run_until_killed(timeout_s=60) == KILLED_EXIT, killer.log_tail()
            assert mailbox() == 1

            worker = await killer.start(None)
            try:
                assert await until(_status(db, task.id, "done", "failed"), timeout_s=60), (
                    killer.log_tail()
                )
            finally:
                await killer.stop(worker)
        finally:
            runner.disconnect()

    row = task_row(db, task.id)
    assert row["enrichment_status"] == "done", killer.log_tail()
    assert row["first_action"] == result["first_action"]
    assert mailbox() == 1
    assert len(runner.runs()) == 1
    applied = rows(
        db,
        "SELECT count(*) AS n FROM task_changes WHERE task_id = %s AND after ? 'estimate_minutes'",
        task.id,
    )
    assert applied == [{"n": 1}]
