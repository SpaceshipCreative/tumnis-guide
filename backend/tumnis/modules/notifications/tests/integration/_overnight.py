"""Helpers for the overnight batch test (P4-04). No assertions: they queue the review items
an unattended night leaves (an unattended run's `result` and an `unattended_refused`
refusal, both marked `batch: "overnight"` with their release time), fire the release tick
and read the notification rows as the owner.

- `RELEASE_AT`: 08:45 EDT on Tuesday 2026-03-10, the first working hour (09:00, the default)
  minus 15 minutes after Monday's 22:00 to 06:00 window.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tumnis.modules.notifications.tests.integration._push import rows

if TYPE_CHECKING:
    from tumnis.modules.notifications.tests.integration._delivery import DeliveryWorld

RELEASE_AT = datetime(2026, 3, 10, 12, 45, tzinfo=UTC)
WINDOW_START = datetime(2026, 3, 10, 2, 0, tzinfo=UTC)


async def overnight_result(world: DeliveryWorld, task_id: UUID) -> UUID:
    """The `result` review item an unattended run leaves (P2-04's kind, batched overnight)."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    async with tenant_session(world.ctx("system")) as s:
        item = await tasks.add_review_item(
            "result",
            target=tasks.TargetRef(type="task", id=task_id),
            project_id=None,
            payload={
                "run_id": str(uuid.uuid4()),
                "outcome": "done",
                "summary": "Fixed the footer link",
                "batch": "overnight",
                "release_at": RELEASE_AT.isoformat(),
            },
            dedupe_key=f"result:{uuid.uuid4()}",
            session=s,
        )
    await world.settle()
    return UUID(str(item))


async def overnight_refusal(world: DeliveryWorld, task_id: UUID) -> UUID:
    """The `unattended_refused` review item the tick raises for a tainted queued task."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    async with tenant_session(world.ctx("system")) as s:
        item = await tasks.add_review_item(
            "unattended_refused",
            target=tasks.TargetRef(type="task", id=task_id),
            project_id=None,
            payload={
                "refusal": "tainted",
                "reason": "From outside content: needs you",
                "window_start": WINDOW_START.isoformat(),
                "batch": "overnight",
                "release_at": RELEASE_AT.isoformat(),
            },
            dedupe_key=f"unattended:{task_id}:{WINDOW_START.isoformat()}",
            session=s,
        )
    await world.settle()
    return UUID(str(item))


async def release(world: DeliveryWorld, at: datetime) -> None:
    """One run of the scheduled overnight release at `at`, then settle."""
    workflows = importlib.import_module("tumnis.modules.notifications.workflows")
    await workflows.release_overnight(at, None)
    await world.settle()


def notifications(world: DeliveryWorld) -> list[dict[str, Any]]:
    return rows(
        world.db,
        "SELECT * FROM notifications WHERE workspace_id = %s ORDER BY created_at, id",
        world.workspace.id,
    )
