"""A task-targeted review item added without a project keeps its task's project (P1-13
review follow-up): the row stores the resolved project id, so the blocking-impact
projection finds it when the task's subtree changes.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.tasks.tests.conftest import owner_rows
from tumnis.modules.tasks.tests.integration.test_review_queue import (
    LABEL_KIND,
    _deliver,
    _label_payload,
)

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tumnis.modules.tasks.tests.conftest import MakeSubtask, MakeTask

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _unscoped_label_item(task: Any) -> uuid.UUID:
    from tumnis.modules.tasks import api  # noqa: PLC0415

    return await api.add_review_item(
        LABEL_KIND,
        target=api.TargetRef(type="task", id=task.id),
        project_id=None,
        payload=_label_payload(),
    )


@pytest.mark.req("FR-6.1")
@pytest.mark.wp("P1-13")
async def test_task_item_without_project_stores_and_refreshes(
    make_task: MakeTask, make_subtask: MakeSubtask, db: DbUrls
) -> None:
    task = await make_task(label="human", estimate_minutes=10)
    item = await _unscoped_label_item(task)
    [(project_id,)] = owner_rows(db, "SELECT project_id FROM review_items WHERE id = %s", (item,))
    assert project_id == task.project_id

    await make_subtask(task, label="human", estimate_minutes=60)
    assert await _deliver(db, "tasks.refresh_review_impact", "task.created") > 0
    [(impact,)] = owner_rows(db, "SELECT blocking_impact FROM review_items WHERE id = %s", (item,))
    assert float(impact) == pytest.approx(2 + 70 / 30)
