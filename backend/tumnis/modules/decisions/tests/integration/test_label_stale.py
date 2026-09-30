"""A label decided for a title the task no longer has is not written (P1-07, FR-4.1): the
retitle starts its own label, so only the answer for the current title lands."""

from __future__ import annotations

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

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture(autouse=True)
def _label_fakes() -> Iterator[None]:
    yield
    reset_label_fakes()


@pytest.mark.req("FR-4.1")
@pytest.mark.wp("P1-07")
async def test_label_for_an_old_title_is_not_written(
    session_client: SessionClient, dbos: Any, fakes: Fakes, db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """The first label is held inside its decision while the title changes; once
    released, both labels are decided, but only the one for the current title is written:
    one AI change row, carrying the second decision's label."""
    gate = Gate()
    jev = gated_jev(gate)
    jev.script("quick_add_label", label_answers("hybrid", 0.93), latency_ms=1)
    use_label_fakes(jev, fakes["decisions.vllm"])
    project = await session_client.post("/v1/projects", json={"name": "Acme", "goal": "Ship"})
    project.raise_for_status()
    try:
        async with relay_running():
            created = await session_client.post(
                "/v1/tasks",
                json={"project_id": project.json()["id"], "title": "Send Acme the invoice"},
            )
            created.raise_for_status()
            task = created.json()
            assert await until(_entered(gate)), "the first label never asked Jev"
            retitled = await session_client.patch(
                f"/v1/tasks/{task['id']}",
                json={"title": "Call Acme about the invoice", "version": task["version"]},
            )
            assert retitled.status_code == 200, retitled.text
            gate.release()
            await until(_decisions(db, task["id"], 2))
            await quiesce(db)
    finally:
        gate.release()

    ai_changes = owner_query(
        db,
        "SELECT change_id FROM task_changes WHERE task_id = %s AND actor = 'system'",
        task["id"],
    )
    assert len(ai_changes) == 1
    [row] = owner_query(
        db, "SELECT title, label::text AS label FROM tasks WHERE id = %s", task["id"]
    )
    assert row == {"title": "Call Acme about the invoice", "label": "hybrid"}


def _entered(gate: Gate) -> Any:
    async def check() -> bool:
        return gate.entered.is_set()

    return check


def _decisions(db: DbUrls, task_id: str, n: int) -> Any:
    async def check() -> bool:
        rows = owner_query(db, "SELECT id FROM decision_log WHERE subject_id = %s", task_id)
        return len(rows) >= n

    return check
