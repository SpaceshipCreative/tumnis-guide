"""The review queue (P1-13, FR-6.1, FR-1.4, FR-11.4, UX 2): `GET /v1/review` orders open
items by blocking impact (downstream tasks and human minutes, times Jev's blocking-impact
factor when it applied), then age; `POST /v1/review/{id}/decide` validates the action
against the kind, closes or snoozes the item and emits `human.decided`, and the owning
module's subscriber applies the effect; `GET /v1/review/count` is the badge.

Subscribers run in-process here: `_deliver` hands the outbox rows of the named events to
one subscriber through `run_subscriber`, as the relay's delivery workflow would.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.tasks.tests.conftest import owner_rows

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.tasks.tests.conftest import Actors, MakeProject, MakeSubtask, MakeTask

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

LABEL_KIND = "low_confidence_label"
PROVISIONING_KIND = "provisioning_failed"
PHASE_ONE_KINDS = (
    "low_confidence_label",
    "decision_unavailable",
    "estimate_outlier",
    PROVISIONING_KIND,
)


async def _deliver(db: DbUrls, subscriber: str, *events: str) -> int:
    """Every outbox row of `events`, oldest first, through `subscriber`; how many ran."""
    import tumnis.wiring  # noqa: F401, PLC0415  # registers every module's subscribers
    from tumnis.core.events import EventEnvelope, run_subscriber  # noqa: PLC0415

    rows = owner_rows(db, "SELECT * FROM outbox WHERE name = ANY(%s) ORDER BY id", (list(events),))
    columns = [
        row[0]
        for row in owner_rows(
            db,
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'outbox' ORDER BY ordinal_position",
        )
    ]
    for row in rows:
        await run_subscriber(
            EventEnvelope.from_outbox_row(dict(zip(columns, row, strict=True))), subscriber
        )
    return len(rows)


def _label_payload(suggested: str = "hybrid") -> dict[str, Any]:
    return {
        "suggested": suggested,
        "probabilities": {"human": 0.2, "ai": 0.18, "hybrid": 0.62},
        "reason": "Needs your judgment",
        "decision_id": str(uuid.uuid4()),
    }


async def _label_item(task: Any, **payload: Any) -> uuid.UUID:
    from tumnis.modules.tasks import api  # noqa: PLC0415

    return await api.add_review_item(
        LABEL_KIND,
        target=api.TargetRef(type="task", id=task.id),
        project_id=task.project_id,
        payload=_label_payload(**payload),
        dedupe_key=f"label:{task.id}",
    )


async def _queue(client: SessionClient, **params: Any) -> list[dict[str, Any]]:
    listed = await client.get("/v1/review", params={"limit": 200, **params})
    assert listed.status_code == 200, listed.text
    items: list[dict[str, Any]] = listed.json()["items"]
    return items


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in items]


def _decisions() -> Any:
    from tumnis.modules.decisions import api  # noqa: PLC0415

    return api


def _tasks_api() -> Any:
    from tumnis.modules.tasks import api  # noqa: PLC0415

    return api


def _providers(fakes: Fakes) -> Any:
    from tumnis.modules.decisions.api import Providers  # noqa: PLC0415

    return Providers(jev=fakes["decisions.jev"], vllm=fakes["decisions.vllm"])


def _impact_answer(level: int) -> dict[str, Any]:
    """A confident Score answer at `level` (0 = blocks nothing, 4 = blocks the project)."""
    probabilities = {str(n): (0.96 if n == level else 0.01) for n in range(5)}
    return {
        "type": "score",
        "score": float(level),
        "probabilities": probabilities,
        "confidence": 0.95,
    }


@pytest.mark.req("FR-11.4")
@pytest.mark.wp("P1-13")
@pytest.mark.xfail(strict=True, reason="spec:P1-13")
async def test_jev_score_combines_with_count(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    make_task: MakeTask,
    fakes: Fakes,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-13-05
    Two items whose targets block the same (one Human task of 30 minutes each); Jev scores
    the younger one 4 and the older one 0, both with high confidence: the younger ranks
    first, its factor is 1.5 and the other's 0.5, and each keeps its decision's id.
    """
    decisions = _decisions()

    older_task = await make_task(label="human", estimate_minutes=30)
    younger_task = await make_task(label="human", estimate_minutes=30)
    older = await _label_item(older_task)
    clock.advance(timedelta(minutes=5))
    younger = await _label_item(younger_task)
    before = await _queue(session_client)
    assert _ids(before) == [str(older), str(younger)]  # same count: age decides

    providers = _providers(fakes)
    fakes["decisions.jev"].script("blocking_impact", {"impact": _impact_answer(4)})
    await decisions.assess_blocking_impact(younger, providers=providers, clock=clock)
    fakes["decisions.jev"].script("blocking_impact", {"impact": _impact_answer(0)})
    await decisions.assess_blocking_impact(older, providers=providers, clock=clock)

    after = await _queue(session_client)
    assert _ids(after) == [str(younger), str(older)]
    factors = dict(
        owner_rows(db, "SELECT id, jev_factor FROM review_items WHERE decision_id IS NOT NULL")
    )
    assert factors == {younger: pytest.approx(1.5), older: pytest.approx(0.5)}
    logged = owner_rows(
        db,
        "SELECT r.id FROM review_items r JOIN decision_log d ON d.id = r.decision_id"
        " WHERE d.decision_point = 'blocking_impact' AND d.subject_type = 'review_item'"
        " AND d.subject_id = r.id",
    )
    assert {row[0] for row in logged} == {younger, older}


@pytest.mark.req("FR-6.1")
@pytest.mark.wp("P1-13")
@pytest.mark.xfail(strict=True, reason="spec:P1-13")
async def test_decisions_down_count_alone_orders(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    make_task: MakeTask,
    make_subtask: MakeSubtask,
    fakes: Fakes,
    clock: FixedClock,
) -> None:
    """T-P1-13-06
    Both decision fakes fail: every item keeps factor 1.0, the order equals the
    deterministic order (most downstream work first, then age), no item is lost and no
    `decision_unavailable` item is added for the blocking-impact question.
    """
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415

    decisions = _decisions()

    small = await make_task(label="human", estimate_minutes=15)  # 1 + 0.5
    big = await make_task(label="human", estimate_minutes=60)  # 1 + 2 + subtasks
    await make_subtask(big, label="human", estimate_minutes=30)
    await make_subtask(big, label="ai")
    medium = await make_task(label="hybrid", estimate_minutes=90)  # 1 + 3
    items = {}
    for name, task in (("small", small), ("big", big), ("medium", medium)):
        items[name] = await _label_item(task)
        clock.advance(timedelta(minutes=1))

    providers = _providers(fakes)
    for name in ("decisions.jev", "decisions.vllm"):
        fakes[name].script("blocking_impact", fail=AdapterUnavailable(name, "ask", "down (test)"))
    for item_id in items.values():
        await decisions.assess_blocking_impact(item_id, providers=providers, clock=clock)

    queue = await _queue(session_client)
    assert _ids(queue) == [str(items["big"]), str(items["medium"]), str(items["small"])]
    assert [item["jev_factor"] for item in queue] == [1.0, 1.0, 1.0]
    assert {item["kind"] for item in queue} == {LABEL_KIND}
    counted = await session_client.get("/v1/review/count")
    assert counted.json() == {"count": 3}


@pytest.mark.req("FR-6.1")
@pytest.mark.wp("P1-13")
@pytest.mark.xfail(strict=True, reason="spec:P1-13")
async def test_impact_updates_when_subtree_changes(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    make_task: MakeTask,
    make_subtask: MakeSubtask,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-13-07
    Item A's target blocks 1 task and 10 minutes, item B's 1 task and 90 minutes: B ranks
    first. Adding two Human subtasks of 60 minutes under A's target, once the projection
    subscriber has seen their `task.created`, raises A above B.
    """
    task_a = await make_task(label="human", estimate_minutes=10)
    task_b = await make_task(label="human", estimate_minutes=90)
    item_a = await _label_item(task_a)
    clock.advance(timedelta(minutes=1))
    item_b = await _label_item(task_b)
    assert _ids(await _queue(session_client)) == [str(item_b), str(item_a)]

    await make_subtask(task_a, label="human", estimate_minutes=60)
    await make_subtask(task_a, label="human", estimate_minutes=60)
    assert await _deliver(db, "tasks.refresh_review_impact", "task.created") > 0

    assert _ids(await _queue(session_client)) == [str(item_a), str(item_b)]
    [(impact,)] = owner_rows(
        db, "SELECT blocking_impact FROM review_items WHERE id = %s", (item_a,)
    )
    assert float(impact) == pytest.approx(3 + 130 / 30)


@pytest.mark.req("FR-1.4")
@pytest.mark.wp("P1-13")
@pytest.mark.xfail(strict=True, reason="spec:P1-13")
def test_badge_equals_unreviewed(
    workspace: WorkspaceHandle, actors: Actors, clock: FixedClock, db: DbUrls
) -> None:
    """T-P1-13-08
    A Hypothesis state machine adds items, decides and snoozes them and moves the fixed
    clock: after every step `review_badge_count` equals the model's unreviewed, unsnoozed
    items, and the open queue lists exactly those.
    """
    from hypothesis import HealthCheck, settings  # noqa: PLC0415
    from hypothesis import strategies as st  # noqa: PLC0415
    from hypothesis.stateful import (  # noqa: PLC0415
        Bundle,
        RuleBasedStateMachine,
        invariant,
        rule,
        run_state_machine_as_test,
    )

    from tests._pg import OWNER  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    api = _tasks_api()
    loop = asyncio.new_event_loop()
    run = loop.run_until_complete
    ctx = workspace.ctx

    async def count() -> int:
        async with tenant_session(ctx) as s:
            counted: int = await api.review_badge_count(s, clock.now())
        return counted

    async def listed() -> set[uuid.UUID]:
        async with tenant_session(ctx) as s:
            page = await api.list_review_items(s, now=clock.now(), limit=200)
        return {item.id for item in page.items}

    async def version_of(item_id: uuid.UUID) -> int:
        async with tenant_session(ctx) as s:
            version: int = (await api.get_review_item(s, item_id)).version
        return version

    class Badge(RuleBasedStateMachine):
        items = Bundle("items")

        def __init__(self) -> None:
            super().__init__()
            import psycopg  # noqa: PLC0415

            with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
                conn.execute(b"DELETE FROM review_items")
            self.decided: set[uuid.UUID] = set()
            self.snoozed: dict[uuid.UUID, Any] = {}
            self.added: set[uuid.UUID] = set()

        def expected(self) -> set[uuid.UUID]:
            now = clock.now()
            return {
                item
                for item in self.added - self.decided
                if item not in self.snoozed or self.snoozed[item] <= now
            }

        @rule(target=items)
        def add(self) -> uuid.UUID:
            item: uuid.UUID = run(
                api.add_review_item(
                    LABEL_KIND,
                    target=api.TargetRef(type="task", id=uuid.uuid4()),
                    project_id=None,
                    payload=_label_payload(),
                )
            )
            self.added.add(item)
            return item

        @rule(item=items, action=st.sampled_from(["accept", "reject"]))
        def decide(self, item: uuid.UUID, action: str) -> None:
            if item in self.decided:
                return
            run(
                api.decide_review_item(
                    item,
                    action=action,
                    payload=None,
                    snooze_until=None,
                    version=run(version_of(item)),
                    actor=actors.human,
                    now=clock.now(),
                )
            )
            self.decided.add(item)

        @rule(item=items, minutes=st.integers(min_value=1, max_value=240))
        def snooze(self, item: uuid.UUID, minutes: int) -> None:
            if item in self.decided:
                return
            until = clock.now() + timedelta(minutes=minutes)
            run(
                api.decide_review_item(
                    item,
                    action="snooze",
                    payload=None,
                    snooze_until=until,
                    version=run(version_of(item)),
                    actor=actors.human,
                    now=clock.now(),
                )
            )
            self.snoozed[item] = until

        @rule(minutes=st.integers(min_value=1, max_value=180))
        def advance_clock(self, minutes: int) -> None:
            clock.advance(timedelta(minutes=minutes))

        @invariant()
        def badge_is_unreviewed(self) -> None:
            assert run(count()) == len(self.expected())
            assert run(listed()) == self.expected()

    try:
        run_state_machine_as_test(  # type: ignore[no-untyped-call]  # hypothesis leaves it untyped
            Badge,
            settings=settings(
                max_examples=12,
                stateful_step_count=15,
                deadline=None,
                suppress_health_check=[HealthCheck.too_slow],
            ),
        )
    finally:
        loop.close()


@pytest.mark.req("UX 2")
@pytest.mark.wp("P1-13")
@pytest.mark.xfail(strict=True, reason="spec:P1-13")
async def test_decide_emits_human_decided_and_owner_applies(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    make_task: MakeTask,
    make_project: MakeProject,
    db: DbUrls,
    workspace: WorkspaceHandle,
) -> None:
    """T-P1-13-09
    Accepting a `low_confidence_label` item closes it and emits `human.decided` with the
    R-07 payload; the tasks subscriber then sets the suggested label (source `user`).
    Accepting (retry) a `provisioning_failed` item for a project whose agent is
    `not_provisioned` starts provisioning through the agents subscriber: the project's
    agent profile goes to `provisioning`.
    """
    from tumnis.modules.tasks import api  # noqa: PLC0415

    task = await make_task()
    payload = _label_payload("hybrid")
    item = await api.add_review_item(
        LABEL_KIND,
        target=api.TargetRef(type="task", id=task.id),
        project_id=task.project_id,
        payload=payload,
    )
    [listed] = await _queue(session_client)
    decided = await session_client.post(
        f"/v1/review/{item}/decide", json={"action": "accept", "version": listed["version"]}
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["decision"] == "accept"
    assert decided.json()["decided_at"] is not None
    assert await _queue(session_client) == []

    [event] = [
        row[0] for row in owner_rows(db, "SELECT payload FROM outbox WHERE name = 'human.decided'")
    ]
    assert event["item_kind"] == LABEL_KIND
    assert event["item_id"] == str(item)
    assert (event["target_type"], event["target_id"]) == ("task", str(task.id))
    assert event["decision"] == "accept"
    assert event["decision_id"] == payload["decision_id"]
    await _deliver(db, "tasks.apply_review_decision", "human.decided")
    [(label, source)] = owner_rows(
        db, "SELECT label::text, label_source FROM tasks WHERE id = %s", (task.id,)
    )
    assert (label, source) == ("hybrid", "user")

    project = await make_project()
    owner_rows_insert = (
        "INSERT INTO agent_profiles (workspace_id, name, role, project_id, transport, endpoint,"
        " status, created_by) VALUES (%s, %s, 'project', %s, 'mcp_endpoint',"
        " 'https://agent.example.org/mcp', 'not_provisioned', 'system') RETURNING id"
    )
    [(profile_id,)] = owner_rows(db, owner_rows_insert, (workspace.id, "proj-retry", project.id))
    failed = await api.add_review_item(
        PROVISIONING_KIND,
        target=api.TargetRef(type="project", id=project.id),
        project_id=project.id,
        payload={"project_id": str(project.id), "error": "timeout", "mode": "create"},
    )
    version = next(i["version"] for i in await _queue(session_client) if i["id"] == str(failed))
    retried = await session_client.post(
        f"/v1/review/{failed}/decide", json={"action": "accept", "version": version}
    )
    assert retried.status_code == 200, retried.text
    await _deliver(db, "agents.apply_review_decision", "human.decided")
    [(status,)] = owner_rows(db, "SELECT status FROM agent_profiles WHERE id = %s", (profile_id,))
    assert status == "provisioning"


@pytest.mark.req("FR-6.1")
@pytest.mark.wp("P1-13")
@pytest.mark.xfail(strict=True, reason="spec:P1-13")
async def test_invalid_action_for_kind_rejected(
    app: FastAPI,
    session_client: SessionClient,
    make_task: MakeTask,
    make_project: MakeProject,
    db: DbUrls,
) -> None:
    """T-P1-13-10
    `edit` on a kind without `edit` (`provisioning_failed`) and `approve` on every phase 1
    kind return 422 `action_not_allowed`; a snooze without `snooze_until` is 422; a stale
    `version` returns 409 `stale_version`. None of them changes the item.
    """
    from tumnis.modules.tasks import api  # noqa: PLC0415

    kinds = api.review_kinds()
    for kind in PHASE_ONE_KINDS:
        assert kind in kinds, kind
        assert "approve" not in kinds[kind].actions

    project = await make_project()
    failed = await api.add_review_item(
        PROVISIONING_KIND,
        target=api.TargetRef(type="project", id=project.id),
        project_id=project.id,
        payload={"project_id": str(project.id), "error": "timeout", "mode": "create"},
    )
    label = await _label_item(await make_task())
    by_id = {item["id"]: item for item in await _queue(session_client)}

    async def decide(item: uuid.UUID, **body: Any) -> Any:
        body.setdefault("version", by_id[str(item)]["version"])
        return await session_client.post(f"/v1/review/{item}/decide", json=body)

    edit = await decide(failed, action="edit", payload={"label": "ai"})
    assert (edit.status_code, edit.json()["code"]) == (422, "action_not_allowed"), edit.text
    for item in (failed, label):
        approve = await decide(item, action="approve")
        assert (approve.status_code, approve.json()["code"]) == (422, "action_not_allowed")
    no_time = await decide(label, action="snooze")
    assert no_time.status_code == 422, no_time.text
    stale = await decide(label, action="accept", version=by_id[str(label)]["version"] + 7)
    assert (stale.status_code, stale.json()["code"]) == (409, "stale_version"), stale.text

    assert owner_rows(db, "SELECT count(*) FROM review_items WHERE decided_at IS NOT NULL"
                      " OR snoozed_until IS NOT NULL") == [(0,)]  # fmt: skip
    assert owner_rows(db, "SELECT count(*) FROM outbox WHERE name = 'human.decided'") == [(0,)]
