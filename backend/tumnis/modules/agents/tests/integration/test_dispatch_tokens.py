"""Every dispatch carries a live task token (P2-02, FR-5.4, R-27).

The fake runner runs in strict mode: it validates every `run` packet against
schemas/packet/v1/task_packet.json, refuses one without `callback.task_token`, and calls
`list_tasks` back with the token before it answers; a refused packet or a failed callback
fails the run.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests.fixtures import REPO_ROOT
from tumnis.modules.agents.tests.contract.base import SCRIPTED_OUTPUT

if TYPE_CHECKING:
    from dbos import DBOS

    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

PLAN_OUTPUT: dict[str, Any] = json.loads(
    (REPO_ROOT / "backend/tests/contract/fixtures/planning/result/v1.json").read_text()
)
# kind -> (profile name, role, skill, output schema, scripted output)
DISPATCHES: dict[str, tuple[str, str, str, tuple[str, str], dict[str, Any]]] = {
    "enrich": ("acme-site", "project", "enrich", ("enrichment", "result"), SCRIPTED_OUTPUT),
    "plan": ("tumnis-master", "master", "plan", ("planning", "result"), PLAN_OUTPUT),
    "task": ("acme-site", "project", "work", ("result", "task_result"), {"summary": "Done"}),
}


async def _packet(
    kind: str, profile_id: uuid.UUID, task: Any, skill: str, out: tuple[str, str]
) -> Any:
    """The run's packet: `build_packet` for a task run; for enrich and plan (whose builders
    are P1-17's and P1-11's) the phase 1 shape with the task's project in its body."""
    from tumnis.modules.agents.api import RunKind, SchemaRef, TaskPacket  # noqa: PLC0415
    from tumnis.modules.agents.packet_builder import build_packet, render_prompt  # noqa: PLC0415

    run_id = uuid.uuid4()
    if kind == "task":
        return await build_packet(
            RunKind.TASK, task_id=task.id, run_id=run_id, profile_id=profile_id
        )
    ref = SchemaRef(family=out[0], name=out[1], version=1)
    body = {"task_id": str(task.id), "project": {"id": str(task.project_id)}}
    return TaskPacket(
        kind=RunKind(kind),
        run_id=run_id,
        profile_id=profile_id,
        skill=skill,
        output_schema=ref,
        correlation_id=f"run:{run_id}",
        timeout_s=60,
        prompt_text=render_prompt(skill, ref, body),
        body=body,
    )


@pytest.mark.req("FR-5.4")
@pytest.mark.wp("P2-02")
@pytest.mark.parametrize("kind", list(DISPATCHES))
async def test_every_dispatch_carries_a_valid_token(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    kind: str,
) -> None:
    """T-P2-02-15
    Enrichment, planning and task dispatches through the strict fake runner all carry a
    live token: the packet validates, the runner's `list_tasks` callback with
    `callback.task_token` succeeds and the run succeeds; once the run ends the token is
    dead. A packet without a token is refused by the strict fake.
    """
    from tests._mcp import make_world  # noqa: PLC0415
    from tumnis.core.principal import AuthFailure  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.agents import workflows  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    name, role, skill, out, output = DISPATCHES[kind]
    world = await make_world(workspace, clock)
    runner = fake_runner(profiles=[name], strict=True)
    runner.script(name, skill, output)
    profile_id = fake_runner.register_profile(
        name,
        runner=runner,
        role=role,  # type: ignore[arg-type]
        project_id=world.projects["A"] if role == "project" else None,
    )
    key = await auth.create_key(
        workspace.ctx,
        auth.KeyIn(name=f"{name} key", scopes=["tasks:read", "tasks:write", "context:read"]),
        now=clock.now(),
    )
    await agents.set_profile_key(workspace.ctx, profile_id, key.id, now=clock.now())
    task = await world.task("A", title="Tidy the Acme footer", label="ai", estimate_minutes=None)

    packet = await _packet(kind, profile_id, task, skill, out)
    handle = await workflows.start_run_skill(workspace.id, packet)
    outcome = await asyncio.wait_for(handle.get_result(), 30)

    assert runner.strict_failures == []
    assert outcome["status"] == "succeeded", outcome
    sent = [run for run in runner.runs() if run.run_id == packet.run_id]
    assert len(sent) == 1
    token = sent[0].packet["callback"]["task_token"]
    assert isinstance(token, str)
    assert token.startswith("tmt_")
    assert runner.callbacks == [(packet.run_id, 200)]
    assert isinstance(await auth.authenticate_bearer(token, now=clock.now()), AuthFailure)

    # The strict fake refuses a packet that carries no token.
    bare = packet.model_dump(mode="json")
    bare["callback"] = None
    assert runner.check_packet(bare) is not None


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
async def test_run_end_erases_the_token_from_the_stored_message(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
) -> None:
    """Scott decision 31 (P2-02): once the run ends, the `run` mailbox message stored for it
    no longer holds the task token (it reads `[redacted]`), so no database backup or dump
    holds a live or recent token.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from tests._mcp import make_world  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.agents import workflows  # noqa: PLC0415
    from tumnis.modules.agents.models import RunnerMessage  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    name, role, skill, out, output = DISPATCHES["task"]
    world = await make_world(workspace, clock)
    runner = fake_runner(profiles=[name], strict=True)
    runner.script(name, skill, output)
    profile_id = fake_runner.register_profile(
        name,
        runner=runner,
        role=role,  # type: ignore[arg-type]
        project_id=world.projects["A"],
    )
    key = await auth.create_key(
        workspace.ctx,
        auth.KeyIn(name=f"{name} key", scopes=["tasks:read", "tasks:write", "context:read"]),
        now=clock.now(),
    )
    await agents.set_profile_key(workspace.ctx, profile_id, key.id, now=clock.now())
    task = await world.task("A", title="Tidy the Acme footer", label="ai", estimate_minutes=None)

    packet = await _packet("task", profile_id, task, skill, out)
    handle = await workflows.start_run_skill(workspace.id, packet)
    outcome = await asyncio.wait_for(handle.get_result(), 30)
    assert outcome["status"] == "succeeded", outcome
    sent = [run for run in runner.runs() if run.run_id == packet.run_id]
    token = sent[0].packet["callback"]["task_token"]
    assert token.startswith("tmt_")

    messages = RunnerMessage.__table__
    async with tenant_session(workspace.ctx) as s:
        payload = await s.scalar(
            select(messages.c.payload).where(
                messages.c.message_id == uuid.uuid5(packet.run_id, "run")
            )
        )
    assert payload is not None
    assert payload["packet"]["callback"]["task_token"] == agents.REDACTED
    assert token not in json.dumps(payload)


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
async def test_master_run_token_reaches_no_project(
    app: Any, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """Scott decision 30 (P2-02): a plan or notify run on the master profile gets a
    workspace-scoped token with no project: its scopes are a subset of the master key's,
    it lists no project's tasks and is 404 on a project; any other run kind must name its
    project.
    """
    from tests._mcp import http_for, make_world  # noqa: PLC0415
    from tumnis.core.principal import Principal  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    world = await make_world(workspace, clock)
    await world.task("A", title="A task of project A", label="ai", estimate_minutes=None)
    held = frozenset({"tasks:read", "tasks:write", "context:read"})
    master = await auth.create_key(
        workspace.ctx, auth.KeyIn(name="master key", scopes=sorted(held)), now=clock.now()
    )
    for kind in (agents.RunKind.PLAN, agents.RunKind.NOTIFY):
        token = await agents.issue_run_token(
            workspace.ctx,
            run_id=uuid.uuid4(),
            kind=kind,
            project_id=None,
            api_key_id=master.id,
            now=clock.now(),
        )
        principal = await auth.authenticate_bearer(token, now=clock.now())
        assert isinstance(principal, Principal)
        assert principal.project_ids == frozenset()
        assert set(principal.scopes) == set(agents.run_token_scopes(kind, held))
        project_a = {"project_id": str(world.projects["A"])}
        async with http_for(app, token) as http:
            everything = await http.get("/v1/tasks")
            in_project_a = await http.get("/v1/tasks", params=project_a)
        assert everything.status_code == 200, everything.text
        assert everything.json()["items"] == []
        assert in_project_a.status_code == 404, in_project_a.text

    with pytest.raises(ValueError, match="names its project"):
        await agents.issue_run_token(
            workspace.ctx,
            run_id=uuid.uuid4(),
            kind=agents.RunKind.TASK,
            project_id=None,
            api_key_id=master.id,
            now=clock.now(),
        )
