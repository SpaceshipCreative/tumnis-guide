"""The scripted fake runner of the compose.test stack (P2-04, R-37, A2.1's harness).

With fake adapters a profile whose runner never connected is served by the in-memory
FakeAgent in the worker. A script posted for a task title (`fakes.runner.script(title,
runs)`) is played after each dispatch of that task: stream lines and an artifact become run
events in order, and the result goes through `agents.api.accept_result`, the one result
path. The last packet it received (with its live task token, a test-only exception to
decision 31) is kept for `GET /v1/test/fakes/runner/last-packet`; the token is redacted
when the run ends.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    KEY_SCOPES,
    owner_rows,
    relay,
    user_ctx,
    wait_until,
)

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

TITLE = "Fix footer link"
LINES = ["Reading the footer component", "git.checkout", "src/footer.tsx"]
RESULT: dict[str, Any] = {
    "outcome": "done",
    "summary": "Fixed the footer link target",
    "files_touched": [{"path": "src/footer.tsx", "change": "modified"}],
    "links": [
        {
            "kind": "pull_request",
            "url": "https://git.example.com/acme/site/pull/7",
            "label": "Pull request 7",
        }
    ],
}
SCRIPT: dict[str, Any] = {
    "task_title": TITLE,
    "runs": [
        [
            {"stream": {"kind": "log", "text": LINES[0]}},
            {"stream": {"kind": "tool_call", "text": LINES[1]}},
            {"stream": {"kind": "file_touched", "text": LINES[2]}},
            {
                "upload_artifact": {
                    "name": "notes.md",
                    "media_type": "text/markdown",
                    "content": "# Footer notes\n".ljust(200, "."),
                }
            },
            {"result": RESULT},
        ],
        [{"result": {**RESULT, "summary": "The footer link now opens in a new tab"}}],
    ],
}


@pytest.fixture
def scripted(fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stored scripts on (as the worker enables them with fakes), played without pauses."""
    from tumnis.core import fake_scripts  # noqa: PLC0415
    from tumnis.modules.agents import fake_play  # noqa: PLC0415

    monkeypatch.setattr(fake_play, "STEP_PAUSE_S", 0.0)
    monkeypatch.setattr(fake_play, "RESULT_HOLD_S", 0.0)
    fake_scripts.enable()
    try:
        yield
    finally:
        fake_scripts.disable()


async def _project_with_fake_agent(workspace: WorkspaceHandle, clock: FixedClock) -> UUID:
    """A project whose agent profile has a key but no runner: the FakeAgent serves it."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ctx = user_ctx(workspace)
    async with tenant_session(ctx) as s:
        project = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name="Acme site"), now=clock.now()
        )
        profile = await agents.register_profile(
            s,
            agents.ProfileIn(
                name="acme-site", role="project", transport="daemon", project_id=project.id
            ),
            now=clock.now(),
        )
    key = await auth.create_key(
        workspace.ctx, auth.KeyIn(name="acme-site key", scopes=KEY_SCOPES), now=clock.now()
    )
    await agents.set_profile_key(workspace.ctx, profile.id, key.id, now=clock.now())
    return project.id


async def _ai_task(workspace: WorkspaceHandle, clock: FixedClock, project_id: UUID) -> UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    ctx = user_ctx(workspace)
    async with tenant_session(ctx) as s:
        task = await tasks.create_task(
            s,
            ctx.actor,
            tasks.TaskCreate(project_id=project_id, title=TITLE, label="ai"),
            now=clock.now(),
        )
    return task.id


def _run_status(db: DbUrls, run_id: UUID) -> str | None:
    rows = owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,))
    return str(rows[0][0]) if rows else None


@pytest.mark.req("FR-5.5", "FR-5.8")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_scripted_fake_plays_each_run_through_accept_result(
    dbos: type[DBOS],
    scripted: None,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """The first run of the scripted task streams its three lines and its artifact as run
    events in order, then its result is accepted: the task is In review with the summary,
    and the run ends `succeeded`. The last packet is that run's, with a live task token
    while the run is open, redacted once it ended; one `run` message so far. A second run
    of the task plays the second script."""
    from tumnis.core import fake_scripts  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.agents.adapters.fake import parse_runner_script  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    key, stored = parse_runner_script(SCRIPT)
    await fake_scripts.put("runner", key, stored)
    project_id = await _project_with_fake_agent(workspace, clock)
    task_id = await _ai_task(workspace, clock, project_id)

    async with relay(db):
        first = await agents.request_run(task_id, agents.RunKind.TASK, ctx=user_ctx(workspace))
        assert await wait_until(lambda: _run_status(db, first) == "succeeded", timeout=30)

        events = owner_rows(
            db,
            "SELECT kind, payload FROM run_events WHERE run_id = %s ORDER BY seq",
            (first,),
        )
        lines = [(kind, payload["text"]) for kind, payload in events if "text" in payload]
        assert lines[:3] == [("log", LINES[0]), ("tool_call", LINES[1]), ("file", LINES[2])]
        [artifact] = [payload for kind, payload in events if kind == "artifact"]
        assert artifact["name"] == "notes.md"
        assert artifact["size"] == 200
        assert owner_rows(db, "SELECT summary FROM results WHERE run_id = %s", (first,)) == [
            (RESULT["summary"],)
        ]
        assert owner_rows(db, "SELECT status::text FROM tasks WHERE id = %s", (task_id,)) == [
            ("in_review",)
        ]

        last = await fake_scripts.last_run_packet()
        assert last is not None
        assert last["run_messages"] == 1
        assert last["packet"]["run_id"] == str(first)
        assert last["packet"]["callback"]["task_token"] == agents.REDACTED

        # The second run (as a reject's rerun would ask) plays the second script.
        async with tenant_session(user_ctx(workspace)) as s:
            current = await tasks.get_task(s, task_id)
            await tasks.change_status(
                s,
                user_ctx(workspace).actor,
                task_id,
                tasks.Status.IN_PROGRESS,
                current.version,
                now=clock.now(),
            )
        second = await agents.request_run(task_id, agents.RunKind.TASK, ctx=user_ctx(workspace))
        assert await wait_until(lambda: _run_status(db, second) == "succeeded", timeout=30)

    assert owner_rows(db, "SELECT summary FROM results WHERE run_id = %s", (second,)) == [
        ("The footer link now opens in a new tab",)
    ]
    last = await fake_scripts.last_run_packet()
    assert last is not None
    assert last["run_messages"] == 2
    assert last["packet"]["run_id"] == str(second)


@pytest.mark.req("FR-5.5")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_last_packet_holds_the_live_token_while_the_run_is_open(
    dbos: type[DBOS],
    scripted: None,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """A task with no script still reaches the fake: its packet is kept with the run's
    live task token while the run waits, and Stop ends the run and redacts it."""
    from tumnis.core import fake_scripts  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    project_id = await _project_with_fake_agent(workspace, clock)
    task_id = await _ai_task(workspace, clock, project_id)

    async with relay(db):
        run_id = await agents.request_run(task_id, agents.RunKind.TASK, ctx=user_ctx(workspace))

        async def received() -> bool:
            last = await fake_scripts.last_run_packet()
            return last is not None and last["packet"]["run_id"] == str(run_id)

        assert await wait_until(received, timeout=30)
        last = await fake_scripts.last_run_packet()
        assert last is not None
        assert last["run_messages"] == 1
        assert str(last["packet"]["callback"]["task_token"]).startswith("tmt_")

        await agents.cancel_run(user_ctx(workspace), run_id, now=clock.now())
        assert await wait_until(lambda: _run_status(db, run_id) == "cancelled", timeout=30)

    last = await fake_scripts.last_run_packet()
    assert last is not None
    assert last["packet"]["callback"]["task_token"] == agents.REDACTED
