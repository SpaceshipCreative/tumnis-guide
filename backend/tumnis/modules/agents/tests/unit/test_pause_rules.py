"""Pause and runaway rules (P2-09, SAF-4, SAF-5): which pause holds a project's runs,
and when a run has created too many tasks."""

from __future__ import annotations

from uuid import UUID

import pytest

ACME = UUID("01950000-0000-7000-8000-000000000a01")
BEACON = UUID("01950000-0000-7000-8000-000000000a02")


@pytest.mark.req("SAF-4", "SAF-5")
@pytest.mark.wp("P2-09")
def test_pause_state_and_task_limit() -> None:
    """T-P2-09-10
    No open pause: running. A pause of another project leaves the project running; a pause
    of the project is `paused_project`; a workspace pause is `paused_workspace` and wins
    over a project pause. A run is over its task limit only once the count after the
    increment exceeds the limit (the 20th task is allowed, the 21st is not; default 20).
    """
    from tumnis.modules.agents.rules import (  # noqa: PLC0415
        MAX_TASKS_PER_RUN_DEFAULT,
        PauseView,
        over_task_limit,
        pause_state,
    )

    workspace = PauseView(scope="workspace", project_id=None)
    acme = PauseView(scope="project", project_id=ACME)
    beacon = PauseView(scope="project", project_id=BEACON)

    assert pause_state([], ACME) == "running"
    assert pause_state([beacon], ACME) == "running"
    assert pause_state([acme], ACME) == "paused_project"
    assert pause_state([beacon, acme], ACME) == "paused_project"
    assert pause_state([workspace], ACME) == "paused_workspace"
    assert pause_state([acme, workspace], ACME) == "paused_workspace"
    assert pause_state([workspace], BEACON) == "paused_workspace"

    assert MAX_TASKS_PER_RUN_DEFAULT == 20
    assert over_task_limit(1, 20) is False
    assert over_task_limit(20, 20) is False
    assert over_task_limit(21, 20) is True
    assert over_task_limit(6, 5) is True
    assert over_task_limit(0, 0) is False
    assert over_task_limit(1, 0) is True
