"""The AI label's `task.updated` carries the time of the event it answers (P1-07, FR-3.9):
the label changes no searchable text, and a person's later edit, stamped by the api's
clock, must stay newer in the search index even when that clock is fixed (tests,
`POST /v1/test/clock`) while the worker's is not."""

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
    use_label_fakes,
)

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture(autouse=True)
def _label_fakes() -> Iterator[None]:
    yield
    reset_label_fakes()


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P1-07")
@pytest.mark.parametrize(("confidence", "field"), [(0.93, "label"), (0.62, "label_suggestion")])
async def test_label_event_has_the_answered_events_time(  # noqa: PLR0917
    session_client: SessionClient,
    dbos: Any,
    fakes: Fakes,
    db: DbUrls,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    confidence: float,
    field: str,
) -> None:
    """Applied or suggested, the label's `task.updated` has the `task.created` event's
    `occurred_at` and document time: the api's fixed clock, not the worker's."""
    jev = fakes["decisions.jev"]
    jev.script("quick_add_label", label_answers("hybrid", confidence), latency_ms=1)
    use_label_fakes(jev, fakes["decisions.vllm"])
    project = await session_client.post("/v1/projects", json={"name": "Acme", "goal": "Ship"})
    project.raise_for_status()
    async with relay_running():
        created = await session_client.post(
            "/v1/tasks", json={"project_id": project.json()["id"], "title": "Send the invoice"}
        )
        created.raise_for_status()
        await quiesce(db)

    events = owner_query(
        db,
        "SELECT name, occurred_at, payload FROM outbox WHERE payload->>'task_id' = %s ORDER BY id",
        created.json()["id"],
    )
    [made] = [e for e in events if e["name"] == "task.created"]
    [labelled] = [
        e for e in events if e["name"] == "task.updated" and field in e["payload"]["changed_fields"]
    ]
    assert made["occurred_at"] == clock.now()
    assert labelled["occurred_at"] == made["occurred_at"]
    assert labelled["payload"]["doc"]["updated_at"] == made["payload"]["doc"]["updated_at"]
