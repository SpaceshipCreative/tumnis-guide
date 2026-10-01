"""Seeded tasks are not enriched on creation (SEED, R-37): a seed describes a moment, not a
history (`tasks.api.seed_task`), so its `task.created` (source `seed`) starts no
enrichment, as the backend acceptance suites already arrange by marking the seed's outbox
rows sent. In the compose.test stack the acceptance project agents read as ready (the fake
runner serves them), and an enrichment of every seeded task would otherwise fill each
project's runs-queue partition with runs waiting on an unscripted fake. A task created any
other way is enriched as before."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from tumnis.core.events import EventEnvelope

pytestmark = [pytest.mark.wp("SEED")]

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def _created(project_id: uuid.UUID, source: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid.uuid4(),
        name="task.created",
        schema_version=1,
        workspace_id=uuid.uuid4(),
        occurred_at=T0,
        actor="system",
        payload={"task_id": str(uuid.uuid4()), "project_id": str(project_id), "source": source},
    )


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Every project has an agent; the enrichments started, by task."""
    from tumnis.modules.agents import events, workflows  # noqa: PLC0415

    found: list[uuid.UUID] = []

    async def provisioned(project_id: uuid.UUID, *, ctx: Any) -> bool:
        del project_id, ctx
        return True

    async def start(
        workspace_id: uuid.UUID, task_id: uuid.UUID, project_id: uuid.UUID, **kwargs: Any
    ) -> None:
        del workspace_id, project_id, kwargs
        found.append(task_id)

    monkeypatch.setattr(workflows, "agent_provisioned", provisioned)
    monkeypatch.setattr(workflows, "start_enrichment", start)
    monkeypatch.setattr(events, "_no_agent", {})
    return found


@pytest.mark.req("FR-4.4")
async def test_a_seeded_task_starts_no_enrichment(started: list[uuid.UUID]) -> None:
    """T-SEED-22"""
    from tumnis.modules.agents.events import enrich_on_create  # noqa: PLC0415

    project = uuid.uuid4()
    await enrich_on_create(_created(project, "seed"))
    assert started == []

    for source in ("quick_add", "api"):
        created = _created(project, source)
        await enrich_on_create(created)
        assert started[-1] == uuid.UUID(created.payload["task_id"])
    assert len(started) == 2
