"""Focus responses and level changes reach the digests (P2-15 with P2-03, FR-10.4,
FR-13.1): `focus.responded` becomes a project `focus_response` entry for the task's project,
and `focus.level_changed` a workspace `focus_setting_changed` entry.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture), level Coach.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.focus.tests.integration._focus import TUESDAY, at, rows

if TYPE_CHECKING:
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-10.4")
@pytest.mark.wp("P2-15")
async def test_responses_join_project_digest(dbos: Any, focus: Focus) -> None:
    """T-P2-15-18
    A still-on-it answer to a check-in becomes one `focus_response` entry in the task's
    project digest; setting the level becomes a `focus_setting_changed` entry in the
    workspace digest.
    """
    task = await focus.task("Write proposal")
    await focus.level("coach")
    started = at(TUESDAY, "10:00")
    await focus.advance(started)
    await focus.move(task, "in_progress")
    await focus.advance(started + timedelta(minutes=25))
    await focus.respond("check_in_due", "still_on_it")

    [entry] = rows(focus.db, "SELECT * FROM digest_entries WHERE kind = 'focus_response'")
    assert (entry["scope"], entry["task_id"]) == ("project", task)
    assert entry["project_id"] is not None
    assert entry["data"]["response"] == "still_on_it"
    [setting] = rows(focus.db, "SELECT * FROM digest_entries WHERE kind = 'focus_setting_changed'")
    assert setting["scope"] == "workspace"
    assert setting["data"]["to"] == "coach"
