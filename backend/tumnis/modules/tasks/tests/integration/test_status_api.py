"""Every transition the matrix leaves blank is refused through REST (P0-18, FR-3.2): 409
`transition_not_allowed` with the task as it is (`current`), for a signed-in human and for
an agent's API key, and the row does not change."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.tasks.tests.unit.transition_matrix import EXPECTED, STATUSES

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tumnis.modules.tasks.tests.conftest import MakeTask, SetStatus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# How a Human task (estimate 30) reaches each status through allowed edges.
PATHS: dict[str, list[tuple[str, str]]] = {
    "backlog": [],
    "today": [("today", "human")],
    "in_progress": [("in_progress", "human")],
    "waiting_on_human": [("in_progress", "human"), ("waiting_on_human", "agent")],
    "in_review": [("in_progress", "human"), ("in_review", "agent")],
    "done": [("in_progress", "human"), ("done", "human")],
}
BLANK = [
    (actor, frm, to)
    for actor in ("human", "agent")
    for frm in STATUSES
    for to in STATUSES
    if not EXPECTED[frm, to, actor, "human"]
]


async def _client(actor: str, request: pytest.FixtureRequest) -> Any:
    if actor == "human":
        return request.getfixturevalue("session_client")
    key_client = request.getfixturevalue("key_client")
    return await key_client(frozenset({"tasks:read", "tasks:write"}))


@pytest.mark.req("FR-3.2")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
@pytest.mark.parametrize(("actor", "frm", "to"), BLANK, ids=[f"{a}:{f}->{t}" for a, f, t in BLANK])
async def test_disallowed_transition_returns_409(  # noqa: PLR0917
    actor: str,
    frm: str,
    to: str,
    app: FastAPI,
    make_task: MakeTask,
    set_status: SetStatus,
    request: pytest.FixtureRequest,
) -> None:
    """T-P0-18-03
    Parametrized over every blank cell of the matrix (a Human task), for a session and for
    a key with `tasks:write`: `POST /v1/tasks/{id}/status {to, version}` answers 409
    `transition_not_allowed` carrying the unchanged task, and the task stays where it was.
    """
    task = await make_task(label="human", estimate_minutes=30)
    for step, who in PATHS[frm]:
        task = await set_status(task, step, who)
    assert task.status == frm

    client = await _client(actor, request)
    refused = await client.post(
        f"/v1/tasks/{task.id}/status", json={"to": to, "version": task.version}
    )
    assert refused.status_code == 409, refused.text
    assert refused.headers["content-type"].startswith("application/problem+json")
    problem = refused.json()
    assert problem["code"] == "transition_not_allowed"
    assert problem["current"]["status"] == frm
    assert problem["current"]["version"] == task.version

    after = await client.get(f"/v1/tasks/{task.id}")
    assert after.status_code == 200, after.text
    assert (after.json()["status"], after.json()["version"]) == (frm, task.version)
