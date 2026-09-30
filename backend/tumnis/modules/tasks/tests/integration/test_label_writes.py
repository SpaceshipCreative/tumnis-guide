"""The label writes behind P1-07 (FR-4.1, FR-4.2, R-07, R-08, R-09), called directly:
`set_ai_label`, `set_label_suggestion`, `update_task` and `undo_task` keep the label, its
metadata, the review item and the decision log consistent with each other (review of
PR #93)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests._labels import owner_query
from tumnis.modules.tasks.tests.conftest import outbox

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.tasks.tests.conftest import Actors, MakeTask

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _row(db: DbUrls, task_id: uuid.UUID) -> dict[str, Any]:
    [row] = owner_query(
        db,
        "SELECT label::text AS label, label_source, label_reason, label_confidence, "
        "label_decision_id, label_suggestion::text AS label_suggestion, version "
        "FROM tasks WHERE id = %s",
        task_id,
    )
    return row


def _open_label_items(db: DbUrls, task_id: uuid.UUID) -> list[dict[str, Any]]:
    return owner_query(
        db,
        "SELECT payload FROM review_items WHERE kind = 'low_confidence_label' "
        "AND target_id = %s AND decided_at IS NULL AND deleted_at IS NULL",
        task_id,
    )


class _Writes:
    """The label writes, each in its own transaction, as the worker and a person make them."""

    def __init__(self, workspace: WorkspaceHandle, actors: Actors, clock: FixedClock) -> None:
        self.workspace, self.actors, self.clock = workspace, actors, clock

    def _session(self, who: Any) -> Any:
        from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415

        return tenant_session(WorkspaceContext(self.workspace.id, who))

    async def ai_label(
        self, task_id: uuid.UUID, label: str, reason: str, decision_id: uuid.UUID
    ) -> uuid.UUID | None:
        from tumnis.modules.tasks import api  # noqa: PLC0415

        async with self._session(self.actors.system) as s:
            return await api.set_ai_label(
                s,
                task_id,
                label=api.Label(label),
                source="jev",
                reason=reason,
                confidence=0.93,
                decision_id=decision_id,
                now=self.clock.now(),
            )

    async def suggest(
        self, task_id: uuid.UUID, label: str, reason: str, decision_id: uuid.UUID
    ) -> bool:
        from tumnis.modules.tasks import api  # noqa: PLC0415

        async with self._session(self.actors.system) as s:
            return await api.set_label_suggestion(
                s,
                task_id,
                suggestion=api.Label(label),
                reason=reason,
                confidence=0.41,
                decision_id=decision_id,
                now=self.clock.now(),
            )

    async def person_labels(self, task_id: uuid.UUID, label: str, version: int) -> Any:
        from tumnis.modules.tasks import api  # noqa: PLC0415

        async with self._session(self.actors.human) as s:
            return await api.update_task(
                s,
                self.actors.human,
                task_id,
                api.TaskPatch(label=api.Label(label), version=version),
                version,
                now=self.clock.now(),
            )

    async def undo(self, task_id: uuid.UUID, change_id: uuid.UUID, version: int) -> Any:
        from tumnis.modules.tasks import api  # noqa: PLC0415

        async with self._session(self.actors.human) as s:
            return await api.undo_task(
                s, self.actors.human, task_id, change_id, version, now=self.clock.now()
            )


@pytest.fixture
def writes(workspace: WorkspaceHandle, actors: Actors, clock: FixedClock) -> _Writes:
    return _Writes(workspace, actors, clock)


@pytest.mark.req("FR-4.2")
@pytest.mark.wp("P1-07")
async def test_accepting_the_ai_label_records_a_human_decision(
    db: DbUrls, make_task: MakeTask, writes: _Writes
) -> None:
    """A person who sets the very label the AI applied makes it theirs: one `human.decided`
    (`label_override`, `overridden = false`) records the acceptance."""
    task = await make_task()
    decision = uuid.uuid4()
    await writes.ai_label(task.id, "hybrid", "Needs a person to send it", decision)

    await writes.person_labels(task.id, "hybrid", _row(db, task.id)["version"])

    assert _row(db, task.id)["label_source"] == "user"
    [decided] = outbox(db, "human.decided")
    assert decided["item_kind"] == "label_override"
    assert decided["decision"] == "hybrid"
    assert decided["payload"] == {"value": "hybrid", "overridden": False}
    assert decided["decision_id"] == str(decision)


@pytest.mark.req("R-09")
@pytest.mark.wp("P1-07")
async def test_undoing_an_ai_label_restores_the_label_metadata_before_it(
    db: DbUrls, make_task: MakeTask, writes: _Writes
) -> None:
    """Undoing a second AI label brings back the first label with its own reason,
    confidence and decision; undoing an AI label that replaced a suggestion brings the
    suggestion back."""
    task = await make_task()
    first, second = uuid.uuid4(), uuid.uuid4()
    await writes.ai_label(task.id, "hybrid", "Needs a person to send it", first)
    before = _row(db, task.id)
    change = await writes.ai_label(task.id, "ai", "A draft the agent can write", second)
    assert change is not None

    await writes.undo(task.id, change, _row(db, task.id)["version"])

    after = _row(db, task.id)
    for field in ("label", "label_source", "label_reason", "label_confidence"):
        assert after[field] == before[field], field
    assert after["label_decision_id"] == first

    other = await make_task()
    suggested, applied = uuid.uuid4(), uuid.uuid4()
    await writes.suggest(other.id, "ai", "Maybe the agent drafts it", suggested)
    change = await writes.ai_label(other.id, "human", "A phone call", applied)
    assert change is not None

    await writes.undo(other.id, change, _row(db, other.id)["version"])

    restored = _row(db, other.id)
    assert (restored["label"], restored["label_suggestion"]) == (None, "ai")
    assert restored["label_reason"] == "Maybe the agent drafts it"
    assert restored["label_decision_id"] == suggested


@pytest.mark.req("R-08")
@pytest.mark.wp("P1-07")
async def test_a_suggestion_after_an_ai_label_leaves_the_label_suggested(
    db: DbUrls, make_task: MakeTask, writes: _Writes
) -> None:
    """A low-confidence answer for a task the AI had labelled (a retitle's relabel) moves
    the task to the suggested state: no confirmed label is left beside the new reason."""
    task = await make_task()
    await writes.ai_label(task.id, "hybrid", "Needs a person to send it", uuid.uuid4())

    assert await writes.suggest(task.id, "ai", "Maybe the agent drafts it", uuid.uuid4())

    row = _row(db, task.id)
    assert (row["label"], row["label_source"]) == (None, None)
    assert (row["label_suggestion"], row["label_reason"]) == ("ai", "Maybe the agent drafts it")


@pytest.mark.req("R-08")
@pytest.mark.wp("P1-07")
async def test_a_new_suggestion_replaces_the_open_review_item(
    db: DbUrls, make_task: MakeTask, writes: _Writes
) -> None:
    """A second low-confidence answer leaves one open review item, and it carries the new
    suggestion, reason and decision."""
    task = await make_task()
    await writes.suggest(task.id, "ai", "Maybe the agent drafts it", uuid.uuid4())
    latest = uuid.uuid4()

    await writes.suggest(task.id, "human", "Maybe a phone call", latest)

    [item] = _open_label_items(db, task.id)
    assert item["payload"]["suggested"] == "human"
    assert item["payload"]["reason"] == "Maybe a phone call"
    assert item["payload"]["decision_id"] == str(latest)


async def _deliver(db: DbUrls, subscriber: str, event: str) -> None:
    """Every outbox row of `event`, oldest first, through `subscriber`."""
    import tumnis.wiring  # noqa: F401, PLC0415  # registers every module's subscribers
    from tumnis.core.events import EventEnvelope, run_subscriber  # noqa: PLC0415

    for row in owner_query(db, "SELECT * FROM outbox WHERE name = %s ORDER BY id", event):
        await run_subscriber(EventEnvelope.from_outbox_row(row), subscriber)


@pytest.mark.req("R-07")
@pytest.mark.wp("P1-07")
async def test_accepting_a_suggestion_in_review_records_one_human_decision(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    dbos: type[DBOS],
    db: DbUrls,
    make_task: MakeTask,
    writes: _Writes,
) -> None:
    """Accepting a `low_confidence_label` item in the review queue (P1-13) emits one
    `human.decided`; applying it sets the label as the person's without a second one
    (`apply_review_decision` passes `label_override=False`)."""
    task = await make_task()
    await writes.suggest(task.id, "hybrid", "Maybe a person sends it", uuid.uuid4())
    listed = await session_client.get("/v1/review", params={"limit": 200})
    assert listed.status_code == 200, listed.text
    [item] = [i for i in listed.json()["items"] if i["target_id"] == str(task.id)]

    decided = await session_client.post(
        f"/v1/review/{item['id']}/decide", json={"action": "accept", "version": item["version"]}
    )
    assert decided.status_code == 200, decided.text
    await _deliver(db, "tasks.apply_review_decision", "human.decided")

    row = _row(db, task.id)
    assert (row["label"], row["label_source"]) == ("hybrid", "user")
    assert len(outbox(db, "human.decided")) == 1
