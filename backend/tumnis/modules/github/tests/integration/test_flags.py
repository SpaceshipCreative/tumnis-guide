"""Red checks in review (P2-13, FR-12.1): when a pull request's checks turn red, the open
result review items that link it are flagged `checks_red`; green clears the flag."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.github.tests.integration._github import (
    OPEN_GREEN,
    OPEN_RED,
    add_result_item,
    link,
    new_task,
    outbox,
    rows,
    set_settings,
)

if TYPE_CHECKING:
    from uuid import UUID

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.modules.github.adapters.fake import FakeGitHubStatus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _flags(db: DbUrls, item_id: UUID) -> list[str]:
    (row,) = rows(db, "SELECT flags FROM review_items WHERE id = %s", item_id)
    flags: list[str] = row["flags"]
    return flags


async def _deliver(db: DbUrls, workspace: WorkspaceHandle, row: dict[str, Any]) -> None:
    """Hands an outbox row to the tasks subscriber as the relay would."""
    from tests.fixtures import make_envelope  # noqa: PLC0415
    from tumnis.core.events import get_subscriber  # noqa: PLC0415
    from tumnis.modules.tasks import events  # noqa: F401, PLC0415  # registers the subscriber

    envelope = make_envelope(row["name"], row["payload"], workspace, row["event_id"])
    await get_subscriber("tasks.flag_red_checks").handler(envelope)


@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
@pytest.mark.xfail(strict=True, reason="spec:P2-13")
async def test_result_with_red_checks_is_flagged(
    app_db: DbUrls, workspace: WorkspaceHandle, github: FakeGitHubStatus
) -> None:
    """T-P2-13-07
    A result review item links brio-example/api#7 (its link names the pull request's
    files page). A refresh finds a failed check run: `artifact.updated` goes out, and the
    `tasks.flag_red_checks` subscriber flags the open result item `checks_red` (delivered
    twice, still one flag), leaving a decided item for the same pull request and an item
    for another one alone. When the run is green again, the next refresh's event clears
    the flag. A refresh that changes nothing emits nothing.
    """
    from tumnis.modules.github import workflows  # noqa: PLC0415
    from tumnis.modules.github.rules import CheckRunView  # noqa: PLC0415

    await set_settings(workspace.ctx, allowed_repos=["brio-example/api", "acme-example/site"])
    task = await new_task(workspace.ctx)
    red = await link(workspace.ctx, task.id, OPEN_RED)
    await link(workspace.ctx, task.id, OPEN_GREEN)
    item = add_result_item(app_db, workspace.id, task.id, OPEN_RED + "/files")
    decided = add_result_item(app_db, workspace.id, task.id, OPEN_RED, decided=True)
    other = add_result_item(app_db, workspace.id, task.id, OPEN_GREEN)

    await workflows.refresh(str(workspace.id), str(red.artifact_id))
    (event,) = outbox(app_db, "artifact.updated")
    assert event["payload"]["artifact_id"] == str(red.artifact_id)
    assert event["payload"]["checks"]["pr_status"]["checks"] == "red"
    for _ in range(2):
        await _deliver(app_db, workspace, event)
    assert _flags(app_db, item) == ["checks_red"]
    assert _flags(app_db, decided) == []
    assert _flags(app_db, other) == []

    await workflows.refresh(str(workspace.id), str(red.artifact_id))
    assert len(outbox(app_db, "artifact.updated")) == 1  # nothing changed, nothing emitted

    sha = github.pulls[("brio-example", "api", 7)].head.sha
    github.check_runs[("brio-example", "api", sha)] = [
        CheckRunView(name="build", status="completed", conclusion="success"),
        CheckRunView(name="test", status="completed", conclusion="success"),
    ]
    await workflows.refresh(str(workspace.id), str(red.artifact_id))
    first, green = outbox(app_db, "artifact.updated")
    assert first == event
    assert green["payload"]["checks"]["pr_status"]["checks"] == "green"
    await _deliver(app_db, workspace, green)
    assert _flags(app_db, item) == []
