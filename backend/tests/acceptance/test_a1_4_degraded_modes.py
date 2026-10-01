"""A1.4 · Degraded modes (phase 1 acceptance, committed red on the phase's first day).

With the master offline, Today falls back to due-date order with a notice; with one
project agent offline, only that project degrades to Jev labels and a pending first action;
with both decision providers down, the label goes to review. Each step carries the spec
marker of the work package that turns it green: step 1 P1-11 (fallback plan), step 2 P1-08
(per-project availability), step 3 P1-07 (the quick-add label step that calls P1-02's
`decide`; the plan lists P1-02, whose own test T-P1-02-06 covers `decide` alone).

Fixtures: `db`, `dbos`, `seed`, `clock` (Monday 2026-03-09 08:30 America/New_York),
`fakes`, `app`, and P1-04's `fake_runner`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.acceptance._phase1 import (
    ACME,
    ACME_AGENT,
    BETA,
    BETA_AGENT,
    MASTER,
    MONDAY,
    MONDAY_PLAN_TIME,
    create_task,
    day_calendar,
    fail_decision_providers,
    get_task,
    inside_a_free_block,
    project_id,
    published_plan,
    rows,
    run_planner_tick,
    runner_result,
    script_label,
    script_run,
    seed_client,
    settle,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.req("A1.4", "FR-4.3", "FR-4.6"),
]

MAX_ITEMS = 5
NULLS_LAST = "9999-12-31"
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def _fallback_key(task: dict[str, Any]) -> tuple[str, int, int, str]:
    """due_on ascending with nulls last, then priority (highest first), then the most
    rolled over (P1-11's `fallback_plan` order), then oldest."""
    return (
        task["due_on"] or NULLS_LAST,
        PRIORITY_RANK[task["priority"]],
        -task["rollover_count"],
        task["created_at"],
    )


@pytest.mark.wp("P1-11")
async def test_master_offline_falls_back_to_due_date_order(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    seed: SeedResult,
    clock: FixedClock,
    fakes: Any,
    app: FastAPI,
    fake_runner: Any,
) -> None:
    """A1.4 step 1
    Given the master profile offline in the fake runner, when `planner_tick` runs at 08:30
    on Monday, then the published plan has `source = "fallback"` and
    `notice = "agent_offline"`, its items follow the due-date order (nulls last, then
    priority, then oldest `created_at`), there are at most 5, every Human or Hybrid item has
    a block inside a free block, and `plan.published` fired once.
    """
    clock.set(MONDAY_PLAN_TIME)
    fake_runner.offline(MASTER)
    http = await seed_client(app, clock)

    await run_planner_tick(clock)
    plan = await settle(lambda: published_plan(http, MONDAY))

    assert plan is not None, "no plan published for Monday"
    assert plan["source"] == "fallback"
    assert plan["notice"] == "agent_offline"
    items = sorted(plan["items"], key=lambda item: item["position"])
    assert 0 < len(items) <= MAX_ITEMS
    tasks = [await get_task(http, item["task_id"]) for item in items]
    assert [t["id"] for t in tasks] == [t["id"] for t in sorted(tasks, key=_fallback_key)]
    free = (await day_calendar(http, MONDAY))["free_blocks"]
    for item, task in zip(items, tasks, strict=True):
        if task["label"] in {"human", "hybrid"}:
            assert item["block"] is not None, f"{task['title']} has no block"
            assert inside_a_free_block(item["block"], free), f"{task['title']} outside free time"
        else:
            assert item["block"] is None
    published = rows(db, "SELECT payload FROM outbox WHERE name = 'plan.published'")
    assert len(published) == 1
    assert published[0]["payload"]["source"] == "fallback"


@pytest.mark.wp("P1-08")
@pytest.mark.xfail(strict=True, reason="spec:P1-08")
async def test_one_project_agent_offline_keeps_others_working(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    seed: SeedResult,
    clock: FixedClock,
    fakes: Any,
    app: FastAPI,
    fake_runner: Any,
) -> None:
    """A1.4 step 2
    Given `acme-site` offline and `beta-app` online, when one task is created in each
    project, then the Acme task gets a Jev label, a placeholder or pending first action, no
    estimate and enrichment status `agent_offline`; the Beta task gets label, first action,
    acceptance criteria and estimate from the fake runner; nothing reaches the dead-letter
    table.
    """
    clock.set(MONDAY_PLAN_TIME)
    script_label(fakes, "human", 0.93)
    fake_runner.offline(ACME_AGENT)
    beta_result = runner_result("enrich_ok_human")
    script_run(fake_runner, BETA_AGENT, "enrich", beta_result)
    http = await seed_client(app, clock)
    acme = await create_task(http, await project_id(http, ACME), "Send Acme the March invoice")
    beta = await create_task(http, await project_id(http, BETA), "Fix the Beta signup form")

    async def both_settled() -> bool:
        a, b = await get_task(http, acme["id"]), await get_task(http, beta["id"])
        return a["enrichment_status"] == "agent_offline" and b["enrichment_status"] == "done"

    await settle(both_settled)
    acme_now, beta_now = await get_task(http, acme["id"]), await get_task(http, beta["id"])

    assert acme_now["enrichment_status"] == "agent_offline"
    assert acme_now["label"] is not None
    assert acme_now["label_source"] == "jev"
    assert acme_now["first_action_source"] in {None, "placeholder"}
    assert acme_now["estimate_minutes"] is None

    assert beta_now["enrichment_status"] == "done"
    assert beta_now["label"] is not None
    assert beta_now["first_action"]
    assert beta_now["first_action_source"] == "agent"
    assert beta_now["acceptance_criteria"]
    assert beta_now["estimate_minutes"] == beta_result["estimate_minutes"]

    assert rows(db, "SELECT event_name, subscriber FROM dead_letters") == []


@pytest.mark.wp("P1-07")
@pytest.mark.xfail(strict=True, reason="spec:P1-07")
async def test_jev_and_vllm_down_sends_label_to_review(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    seed: SeedResult,
    clock: FixedClock,
    fakes: Any,
    app: FastAPI,
    fake_runner: Any,
) -> None:
    """A1.4 step 3
    Given both decision providers failing (Jev and the vLLM fallback), when a task is
    created, then it has no label, a `decision_unavailable` review item exists for it, and
    its decision log row has `outcome = "review"` and `provider = "none"`.
    """
    clock.set(MONDAY_PLAN_TIME)
    fail_decision_providers(fakes)
    http = await seed_client(app, clock)
    task = await create_task(http, await project_id(http, ACME), "Send Acme the March invoice")

    def logged() -> list[dict[str, Any]]:
        return rows(
            db,
            "SELECT provider, outcome FROM decision_log "
            "WHERE subject_type = 'task' AND subject_id = %s",
            task["id"],
        )

    async def decided() -> list[dict[str, Any]]:
        return logged()

    await settle(decided)

    assert (await get_task(http, task["id"]))["label"] is None
    review = rows(
        db,
        "SELECT kind FROM review_items WHERE target_type = 'task' AND target_id = %s "
        "AND kind = 'decision_unavailable' AND decided_at IS NULL",
        task["id"],
    )
    assert len(review) == 1
    assert logged() == [{"provider": "none", "outcome": "review"}]
