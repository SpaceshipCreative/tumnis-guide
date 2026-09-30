"""`apply_enrichment` (P1-08) and the label rules around it, called directly (review of
PR #109): a label the project agent revises to AI drops the estimate (AI work carries
none), and a revised label closes the `low_confidence_label` item whose suggestion it
clears."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests._labels import owner_query

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.tasks.tests.conftest import Actors, MakeTask

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _row(db: DbUrls, task_id: uuid.UUID) -> dict[str, Any]:
    [row] = owner_query(
        db,
        "SELECT label::text AS label, label_source, label_suggestion::text AS "
        "label_suggestion, estimate_minutes, enrichment_status, version "
        "FROM tasks WHERE id = %s",
        task_id,
    )
    return row


def _open_label_items(db: DbUrls, task_id: uuid.UUID) -> list[dict[str, Any]]:
    return owner_query(
        db,
        "SELECT id FROM review_items WHERE kind = 'low_confidence_label' "
        "AND target_id = %s AND decided_at IS NULL AND deleted_at IS NULL",
        task_id,
    )


async def _apply(
    workspace: WorkspaceHandle,
    actors: Actors,
    clock: FixedClock,
    task_id: uuid.UUID,
    version: int,
    **write: Any,
) -> None:
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api  # noqa: PLC0415

    async with tenant_session(WorkspaceContext(workspace.id, actors.system)) as s:
        await api.apply_enrichment(
            s, task_id, api.EnrichmentWrite(**write), version, now=clock.now()
        )


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
async def test_a_label_revised_to_ai_drops_the_estimate(
    db: DbUrls, workspace: WorkspaceHandle, actors: Actors, clock: FixedClock, make_task: MakeTask
) -> None:
    """A pending task a person estimated, revised to AI by the enrichment, keeps no
    estimate; the enrichment ends `done`."""
    task = await make_task(estimate_minutes=45)
    assert _row(db, task.id)["estimate_minutes"] == 45

    await _apply(
        workspace,
        actors,
        clock,
        task.id,
        _row(db, task.id)["version"],
        label="ai",
        label_reason="A draft the agent can write",
    )

    row = _row(db, task.id)
    assert (row["label"], row["label_source"]) == ("ai", "agent")
    assert row["estimate_minutes"] is None
    assert row["enrichment_status"] == "done"


@pytest.mark.req("R-08")
@pytest.mark.wp("P1-08")
async def test_a_revised_label_closes_the_open_suggestion_item(
    db: DbUrls, workspace: WorkspaceHandle, actors: Actors, clock: FixedClock, make_task: MakeTask
) -> None:
    """A task waiting on a low-confidence suggestion that the enrichment labels has no
    suggestion and no open `low_confidence_label` item left for a stale decision."""
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api  # noqa: PLC0415

    task = await make_task()
    async with tenant_session(WorkspaceContext(workspace.id, actors.system)) as s:
        assert await api.set_label_suggestion(
            s,
            task.id,
            suggestion=api.Label("ai"),
            reason="Maybe the agent drafts it",
            confidence=0.41,
            decision_id=uuid.uuid4(),
            now=clock.now(),
        )
    assert len(_open_label_items(db, task.id)) == 1

    await _apply(
        workspace,
        actors,
        clock,
        task.id,
        _row(db, task.id)["version"],
        label="human",
        label_reason="A phone call",
        estimate_minutes=20,
    )

    row = _row(db, task.id)
    assert (row["label"], row["label_suggestion"]) == ("human", None)
    assert row["estimate_minutes"] == 20
    assert _open_label_items(db, task.id) == []
