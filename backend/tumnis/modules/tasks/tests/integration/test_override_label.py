"""Overriding the AI label (P1-07, FR-4.1, FR-4.2): one PATCH makes the label the user's,
records a human decision (`human.decided`, R-07) and the decision's outcome; an AI label
never overwrites a human choice; a title edit relabels unless the human chose."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

from tests._labels import (
    Gate,
    gated_jev,
    label_answers,
    owner_query,
    quiesce,
    relay_running,
    reset_label_fakes,
    until,
    use_label_fakes,
)
from tumnis.modules.tasks.tests.conftest import outbox

if TYPE_CHECKING:
    from pathlib import Path

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, WorkspaceHandle
    from tumnis.modules.tasks.tests.conftest import MakeProject

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture(autouse=True)
def _label_fakes() -> Iterator[None]:
    yield
    reset_label_fakes()


async def _task(http: SessionClient, project_id: str, title: str) -> dict[str, Any]:
    created = await http.post("/v1/tasks", json={"project_id": project_id, "title": title})
    created.raise_for_status()
    body: dict[str, Any] = created.json()
    return body


def _task_row(db: DbUrls, task_id: str) -> dict[str, Any]:
    [row] = owner_query(
        db,
        "SELECT label::text AS label, label_source, label_decision_id, version "
        "FROM tasks WHERE id = %s",
        task_id,
    )
    return row


def _decision_rows(db: DbUrls, task_id: str) -> list[dict[str, Any]]:
    return owner_query(
        db,
        "SELECT id, overridden, final_value FROM decision_log "
        "WHERE subject_type = 'task' AND subject_id = %s ORDER BY created_at",
        task_id,
    )


@pytest.mark.req("FR-4.2")
@pytest.mark.wp("P1-07")
@pytest.mark.xfail(strict=True, reason="spec:P1-07")
async def test_override_emits_human_decided_and_outcome(  # noqa: PLR0917
    session_client: SessionClient,
    dbos: Any,
    fakes: Fakes,
    db: DbUrls,
    workspace: WorkspaceHandle,
    make_project: MakeProject,
    repo_root: Path,
) -> None:
    """T-P1-07-05
    A Jev-labelled task (hybrid) overridden with `PATCH {label: human, version}`: the label
    is the user's (`label_source = user`), one `human.decided` carries R-07's payload
    (`item_kind = label_override`, the task as item and target, the decision `human`, the
    previous label, source and suggestion, the decision's id) and validates against its
    schema; once delivered, the decision's log row has `overridden = true` and
    `final_value = "human"`.
    """
    jev = fakes["decisions.jev"]
    jev.script("quick_add_label", label_answers("hybrid", 0.93))
    use_label_fakes(jev, fakes["decisions.vllm"])
    project = await make_project()
    async with relay_running():
        task = await _task(session_client, str(project.id), "Send Acme the March invoice")
        labelled = await until(lambda: _is_labelled(db, task["id"]))
        assert labelled
        before = _task_row(db, task["id"])

        patched = await session_client.patch(
            f"/v1/tasks/{task['id']}", json={"label": "human", "version": before["version"]}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["label_source"] == "user"
        await quiesce(db)

    [decided] = outbox(db, "human.decided")
    [decision] = _decision_rows(db, task["id"])
    assert decided["item_kind"] == "label_override"
    assert decided["item_id"] == decided["target_id"] == task["id"]
    assert decided["target_type"] == "task"
    assert decided["decision"] == "human"
    assert decided["previous"] == {
        "label": "hybrid",
        "label_source": "jev",
        "label_suggestion": None,
    }
    assert decided["decision_id"] == str(decision["id"])
    schema = json.loads((repo_root / "schemas/events/v1/human.decided.json").read_text())
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]  # noqa: PLC0415

    Draft202012Validator(schema).validate(decided)
    assert decision["overridden"] is True
    assert decision["final_value"] == "human"
    after = _task_row(db, task["id"])
    assert (after["label"], after["label_source"]) == ("human", "user")


async def _is_labelled(db: DbUrls, task_id: str) -> bool:
    return _task_row(db, task_id)["label"] is not None


@pytest.mark.req("FR-4.2")
@pytest.mark.wp("P1-07")
@pytest.mark.xfail(strict=True, reason="spec:P1-07")
async def test_ai_label_never_overwrites_human_choice(  # noqa: PLR0917
    session_client: SessionClient,
    dbos: Any,
    fakes: Fakes,
    db: DbUrls,
    workspace: WorkspaceHandle,
    make_project: MakeProject,
) -> None:
    """T-P1-07-06
    The label workflow is held inside its decision (the fake's call waits on a gate) while
    the user sets the label: once released, `set_ai_label` finds the user's label and
    writes nothing. The label stays the user's and no AI change row exists.
    """
    gate = Gate()
    jev = gated_jev(gate)
    jev.script("quick_add_label", label_answers("hybrid", 0.93), latency_ms=1)
    use_label_fakes(jev, fakes["decisions.vllm"])
    project = await make_project()
    try:
        async with relay_running():
            task = await _task(session_client, str(project.id), "Send Acme the March invoice")
            entered = await until(lambda: _entered(gate))
            assert entered, "the label workflow never asked Jev"
            patched = await session_client.patch(
                f"/v1/tasks/{task['id']}", json={"label": "human", "version": task["version"]}
            )
            assert patched.status_code == 200, patched.text
            gate.release()
            await until(lambda: _decided(db, task["id"]))
            await quiesce(db)
    finally:
        gate.release()

    row = _task_row(db, task["id"])
    assert (row["label"], row["label_source"]) == ("human", "user")
    assert len(_decision_rows(db, task["id"])) == 1
    ai_changes = owner_query(
        db,
        "SELECT change_id FROM task_changes WHERE task_id = %s AND actor = 'system'",
        task["id"],
    )
    assert ai_changes == []


async def _entered(gate: Gate) -> bool:
    return gate.entered.is_set()


async def _decided(db: DbUrls, task_id: str) -> list[dict[str, Any]]:
    return _decision_rows(db, task_id)


@pytest.mark.req("FR-4.1")
@pytest.mark.wp("P1-07")
@pytest.mark.xfail(strict=True, reason="spec:P1-07")
async def test_title_edit_relabels_unless_human_chose(  # noqa: PLR0917
    session_client: SessionClient,
    dbos: Any,
    fakes: Fakes,
    db: DbUrls,
    workspace: WorkspaceHandle,
    make_project: MakeProject,
) -> None:
    """T-P1-07-07
    A `task.updated` naming `title` relabels a Jev-labelled task (a second decision; the
    new answer `ai` is applied); the same title edit on a task whose label the user chose
    asks nothing and keeps the user's label.
    """
    jev = fakes["decisions.jev"]
    jev.script("quick_add_label", label_answers("hybrid", 0.93))
    use_label_fakes(jev, fakes["decisions.vllm"])
    project = await make_project()
    async with relay_running():
        by_jev = await _task(session_client, str(project.id), "Send Acme the March invoice")
        by_user = await _task(session_client, str(project.id), "Call Acme about the invoice")
        await until(lambda: _both_labelled(db, by_jev["id"], by_user["id"]))
        chosen = await session_client.patch(
            f"/v1/tasks/{by_user['id']}",
            json={"label": "human", "version": _task_row(db, by_user["id"])["version"]},
        )
        assert chosen.status_code == 200, chosen.text
        await quiesce(db)

        jev.script("quick_add_label", label_answers("ai", 0.95))
        for task in (by_jev, by_user):
            retitled = await session_client.patch(
                f"/v1/tasks/{task['id']}",
                json={
                    "title": task["title"] + " (draft in the template)",
                    "version": _task_row(db, task["id"])["version"],
                },
            )
            assert retitled.status_code == 200, retitled.text
        await until(lambda: _relabelled(db, by_jev["id"]))
        await quiesce(db)

    assert len(_decision_rows(db, by_jev["id"])) == 2
    assert _task_row(db, by_jev["id"])["label"] == "ai"
    assert _task_row(db, by_jev["id"])["label_source"] == "jev"
    assert len(_decision_rows(db, by_user["id"])) == 1
    user_row = _task_row(db, by_user["id"])
    assert (user_row["label"], user_row["label_source"]) == ("human", "user")


async def _both_labelled(db: DbUrls, first: str, second: str) -> bool:
    return bool(await _is_labelled(db, first) and await _is_labelled(db, second))


async def _relabelled(db: DbUrls, task_id: str) -> bool:
    return bool(_task_row(db, task_id)["label"] == "ai")
