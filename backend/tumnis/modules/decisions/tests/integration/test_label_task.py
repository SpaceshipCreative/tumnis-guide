"""The quick-add label (P1-07, FR-4.1): `task.created` runs `label_task` on the decisions
queue, which asks Jev (the fake here) and applies a confident label with a one-line
reason, or keeps a low-confidence one as a suggestion with a review item. The relay and
the queue run as in the worker: the `dbos` fixture's workers and the relay helper."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

from tests._labels import (
    label_answers,
    owner_query,
    quiesce,
    relay_running,
    reset_label_fakes,
    until,
    use_label_fakes,
)

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, WorkspaceHandle

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.usefixtures("test_cache"),
]

TITLE = "Send Acme the March invoice"


@pytest.fixture(autouse=True)
def _label_fakes() -> Iterator[None]:
    yield
    reset_label_fakes()


async def _project(http: SessionClient) -> str:
    created = await http.post(
        "/v1/projects",
        json={"name": "Acme site", "goal": "Ship the Acme marketing site redesign by June"},
    )
    created.raise_for_status()
    return str(created.json()["id"])


async def _task(http: SessionClient, project_id: str, title: str = TITLE) -> dict[str, Any]:
    created = await http.post("/v1/tasks", json={"project_id": project_id, "title": title})
    created.raise_for_status()
    body: dict[str, Any] = created.json()
    return body


def _row(db: DbUrls, task_id: str) -> dict[str, Any]:
    [row] = owner_query(
        db,
        "SELECT label::text AS label, label_source, label_reason, label_suggestion::text "
        "AS label_suggestion, label_decision_id, version FROM tasks WHERE id = %s",
        task_id,
    )
    return row


def _decisions(db: DbUrls, task_id: str) -> list[dict[str, Any]]:
    return owner_query(
        db,
        "SELECT id, outcome, provider FROM decision_log "
        "WHERE subject_type = 'task' AND subject_id = %s",
        task_id,
    )


@pytest.mark.req("FR-4.1")
@pytest.mark.wp("P1-07")
@pytest.mark.xfail(strict=True, reason="spec:P1-07")
async def test_label_has_one_line_reason(
    session_client: SessionClient, dbos: Any, fakes: Fakes, db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P1-07-02
    Jev answers `hybrid` at 0.93: the decision applies, so the task gets `label = hybrid`,
    `label_source = jev`, the decision's id, and a non-empty one-line `label_reason` under
    80 characters.
    """
    jev = fakes["decisions.jev"]
    jev.script("quick_add_label", label_answers("hybrid", 0.93))
    use_label_fakes(jev, fakes["decisions.vllm"])
    async with relay_running():
        task = await _task(session_client, await _project(session_client))
        await until(lambda: _labelled(db, task["id"]))
    row = _row(db, task["id"])

    assert (row["label"], row["label_source"]) == ("hybrid", "jev")
    reason = row["label_reason"]
    assert reason
    assert "\n" not in reason
    assert len(reason) < 80
    [decision] = _decisions(db, task["id"])
    assert decision["outcome"] == "applied"
    assert row["label_decision_id"] == decision["id"]
    assert row["label_suggestion"] is None


async def _labelled(db: DbUrls, task_id: str) -> bool:
    return _row(db, task_id)["label"] is not None


async def _decided(db: DbUrls, task_id: str) -> list[dict[str, Any]]:
    return _decisions(db, task_id)


def _open_items(db: DbUrls, task_id: str) -> list[dict[str, Any]]:
    return owner_query(
        db,
        "SELECT kind, payload FROM review_items WHERE target_type = 'task' "
        "AND target_id = %s AND decided_at IS NULL AND deleted_at IS NULL",
        task_id,
    )


@pytest.mark.req("FR-4.1")
@pytest.mark.wp("P1-07")
@pytest.mark.xfail(strict=True, reason="spec:P1-07")
async def test_low_confidence_is_suggested_with_review_item(
    session_client: SessionClient, dbos: Any, fakes: Fakes, db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P1-07-03
    Jev answers `hybrid` at 0.62, under the 0.80 threshold: `label` stays NULL (pending),
    `label_suggestion = hybrid` with its reason, and one open `low_confidence_label` review
    item on the task carries the suggestion, the probabilities and the decision's id.
    """
    jev = fakes["decisions.jev"]
    jev.script("quick_add_label", label_answers("hybrid", 0.62))
    use_label_fakes(jev, fakes["decisions.vllm"])
    async with relay_running():
        task = await _task(session_client, await _project(session_client))
        await until(lambda: _decided(db, task["id"]))
        await quiesce(db)
    row = _row(db, task["id"])

    assert row["label"] is None
    assert row["label_source"] is None
    assert row["label_suggestion"] == "hybrid"
    assert row["label_reason"]
    [decision] = _decisions(db, task["id"])
    assert decision["outcome"] == "review"
    [item] = _open_items(db, task["id"])
    assert item["kind"] == "low_confidence_label"
    assert item["payload"]["suggested"] == "hybrid"
    assert item["payload"]["probabilities"]["hybrid"] == pytest.approx(0.62)
    assert item["payload"]["decision_id"] == str(decision["id"])


@pytest.mark.req("FR-4.1")
@pytest.mark.wp("P1-07")
@pytest.mark.xfail(strict=True, reason="spec:P1-07")
async def test_unknown_winner_goes_to_review_without_label(
    session_client: SessionClient, dbos: Any, fakes: Fakes, db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P1-07-04
    `unknown` wins (Jev cannot tell from the title): no label and no suggestion, and one
    open review item on the task asks the human (`decision_unavailable`: ask again or set
    the label by hand).
    """
    jev = fakes["decisions.jev"]
    jev.script("quick_add_label", label_answers("unknown", 0.9))
    use_label_fakes(jev, fakes["decisions.vllm"])
    async with relay_running():
        task = await _task(session_client, await _project(session_client), "Acme thing")
        await until(lambda: _decided(db, task["id"]))
        await quiesce(db)
    row = _row(db, task["id"])

    assert row["label"] is None
    assert row["label_suggestion"] is None
    [decision] = _decisions(db, task["id"])
    assert decision["outcome"] == "review"
    [item] = _open_items(db, task["id"])
    assert item["kind"] == "decision_unavailable"
    assert item["payload"]["decision_id"] == str(decision["id"])


@pytest.mark.req("FR-4.1")
@pytest.mark.wp("P1-07")
@pytest.mark.xfail(strict=True, reason="spec:P1-07")
async def test_duplicate_event_labels_once(
    session_client: SessionClient, dbos: Any, fakes: Fakes, db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P1-07-09
    The same `task.created` delivered twice to the label subscriber asks Jev once and
    writes one decision log row: the workflow is keyed on the event.
    """
    from tumnis.core.events import EventEnvelope, run_subscriber  # noqa: PLC0415

    jev = fakes["decisions.jev"]
    jev.script("quick_add_label", label_answers("hybrid", 0.93))
    use_label_fakes(jev, fakes["decisions.vllm"])
    task = await _task(session_client, await _project(session_client))
    [outbox_row] = owner_query(
        db,
        "SELECT * FROM outbox WHERE name = 'task.created' AND payload->>'task_id' = %s",
        task["id"],
    )
    envelope = EventEnvelope.from_outbox_row(outbox_row)

    await run_subscriber(envelope, "decisions.label_on_create")
    await run_subscriber(envelope, "decisions.label_on_create")
    await until(lambda: _labelled(db, task["id"]))
    await quiesce(db)

    assert len(_decisions(db, task["id"])) == 1
    assert len([c for c in jev.calls if c.point == "quick_add_label"]) == 1
    assert _row(db, task["id"])["label"] == "hybrid"
