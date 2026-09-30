"""What a review item blocks (P1-13, FR-6.1): `downstream` counts the open tasks under the
kind's impact scope and the estimated minutes of the open Human and Hybrid ones.

One project: root R (Human, 30 min) with subtasks C1 (Human, 60 min, open), C2 (Hybrid,
45 min, Done) and C3 (AI, no estimate); root S (Human, 20 min); and, in another project,
Q (Human, 10 min). The caller hands `downstream` the tasks of the scope it reads (the
project's for "project", every task for "workspace").
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

R, C1, C2, C3, S, Q = (uuid.uuid4() for _ in range(6))


def _rules() -> Any:
    from tumnis.modules.tasks import rules  # noqa: PLC0415

    return rules


def _graph(ids: set[uuid.UUID]) -> Any:
    rules = _rules()

    status, label = rules.Status, rules.Label

    def t(task_id: uuid.UUID, parent: uuid.UUID | None, st: Any, lb: Any, est: int | None) -> Any:
        return rules.GraphTask(
            id=task_id, parent_id=parent, status=st, label=lb, estimate_minutes=est
        )

    everything = [
        t(R, None, status.TODAY, label.HUMAN, 30),
        t(C1, R, status.BACKLOG, label.HUMAN, 60),
        t(C2, R, status.DONE, label.HYBRID, 45),
        t(C3, R, status.IN_PROGRESS, label.AI, None),
        t(S, None, status.BACKLOG, label.HUMAN, 20),
        t(Q, None, status.BACKLOG, label.HUMAN, 10),
    ]
    return rules.TaskGraph(tasks=tuple(task for task in everything if task.id in ids))


PROJECT = {R, C1, C2, C3, S}
EVERYTHING = {*PROJECT, Q}

CASES = [
    pytest.param(S, "task", PROJECT, (1, 20), id="target_only"),
    pytest.param(R, "task", PROJECT, (3, 90), id="subtree_done_excluded"),
    pytest.param(C3, "task", PROJECT, (1, 0), id="ai_counts_never_minutes"),
    pytest.param(C2, "task", PROJECT, (0, 0), id="done_target_blocks_nothing"),
    pytest.param(None, "task", PROJECT, (0, 0), id="no_target_task"),
    pytest.param(None, "project", PROJECT, (4, 110), id="project_scope"),
    pytest.param(R, "project", PROJECT, (4, 110), id="project_scope_ignores_target"),
    pytest.param(None, "workspace", EVERYTHING, (5, 120), id="workspace_scope"),
]


@pytest.mark.req("FR-6.1")
@pytest.mark.wp("P1-13")
@pytest.mark.xfail(strict=True, reason="spec:P1-13")
@pytest.mark.parametrize(("target", "scope", "ids", "expected"), CASES)
def test_downstream_scopes(
    target: uuid.UUID | None, scope: str, ids: set[uuid.UUID], expected: tuple[int, int]
) -> None:
    """T-P1-13-04
    Target only, the subtree with Done children excluded, project and workspace scope; AI
    tasks add to the count, never to the minutes.
    """
    assert _rules().downstream(target, scope, _graph(ids)) == expected
