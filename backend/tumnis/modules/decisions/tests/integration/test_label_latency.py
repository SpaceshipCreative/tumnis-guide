"""The quick-add label's latency budget (P1-07, FR-3.3): the label lands within a second
of the task's commit, through the real relay, the events and decisions queues, and the
Jev fake answering at the recording's latency."""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

from tests._labels import (
    label_answers,
    owner_query,
    recorded_latency_ms,
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
    pytest.mark.slow,
    pytest.mark.usefixtures("test_cache"),
]

QUICK_ADDS = 40
BUDGET_MS = 1_000


@pytest.fixture(autouse=True)
def _label_fakes() -> Iterator[None]:
    yield
    reset_label_fakes()


@pytest.mark.req("FR-3.3")
@pytest.mark.wp("P1-07")
async def test_label_p95_under_1s_at_recorded_latency(
    session_client: SessionClient, dbos: Any, fakes: Fakes, db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """T-P1-07-01
    With the Jev fake answering at the `quick_add_label__hybrid` recording's latency, 40
    tasks created through `POST /v1/tasks` one after another are each labelled, and the
    95th percentile of `updated_at` (the label write) minus `created_at` is under 1,000 ms.
    """
    jev = fakes["decisions.jev"]
    jev.script("quick_add_label", label_answers("hybrid", 0.93), latency_ms=recorded_latency_ms())
    use_label_fakes(jev, fakes["decisions.vllm"])
    created = await session_client.post(
        "/v1/projects", json={"name": "Acme site", "goal": "Ship the site"}
    )
    created.raise_for_status()
    project_id = created.json()["id"]

    async def labelled() -> list[dict[str, Any]]:
        rows = owner_query(
            db,
            "SELECT extract(epoch FROM updated_at - created_at) * 1000 AS ms FROM tasks "
            "WHERE project_id = %s AND label IS NOT NULL",
            project_id,
        )
        return rows if len(rows) == QUICK_ADDS else []

    async with relay_running():
        for n in range(1, QUICK_ADDS + 1):
            response = await session_client.post(
                "/v1/tasks",
                json={"project_id": project_id, "title": f"Send Acme invoice {n} of March"},
            )
            response.raise_for_status()
        rows = await until(labelled, timeout_s=60)

    assert len(rows) == QUICK_ADDS
    latencies = sorted(float(row["ms"]) for row in rows)
    p95 = latencies[math.ceil(0.95 * QUICK_ADDS) - 1]
    assert p95 < BUDGET_MS, latencies
