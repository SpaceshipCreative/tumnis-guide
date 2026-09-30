"""Project provisioning (P1-06, FR-2.1, FR-5.10): `project.created` gives the project its
Hermes profile on the agent server exactly once (a redelivered event and a killed worker
included), linking an existing profile checks that it exists, and a failed or timed-out
provision leaves the project usable with agent status `not_provisioned` and one
`provisioning_failed` review item whose accept retries it."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
import pytest

from tests._pg import APP, OWNER

if TYPE_CHECKING:
    from dbos import DBOS, WorkflowHandleAsync

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import AppFactory, WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

SETTLE_S = 20.0
MASTER = "tumnis-master"


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


async def _settle(check: Callable[[], Awaitable[bool] | bool], wait_s: float = SETTLE_S) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while True:
        answer = check()
        if not isinstance(answer, bool):
            answer = await answer
        if answer:
            return
        assert loop.time() < deadline, "condition not met in time"
        await asyncio.sleep(0.1)


def _profile(db: DbUrls, project_id: UUID) -> list[tuple[Any, ...]]:
    return _owner(
        db,
        "SELECT name, status FROM agent_profiles WHERE project_id = %s AND deleted_at IS NULL",
        (project_id,),
    )


def _status_is(db: DbUrls, project_id: UUID, *statuses: str) -> Callable[[], bool]:
    def check() -> bool:
        rows = _profile(db, project_id)
        return len(rows) == 1 and rows[0][1] in statuses

    return check


def _provision_messages(db: DbUrls, profile: str) -> int:
    [(count,)] = _owner(
        db,
        "SELECT count(*) FROM runner_messages WHERE direction = 'out' AND type = 'provision'"
        " AND payload->>'profile' = %s",
        (profile,),
    )
    return int(count)


def _failed_items(db: DbUrls, project_id: UUID) -> list[tuple[Any, ...]]:
    return _owner(
        db,
        "SELECT payload FROM review_items WHERE kind = 'provisioning_failed'"
        " AND project_id = %s AND deleted_at IS NULL",
        (project_id,),
    )


def _provisions(runner: FakeRunner) -> list[Any]:
    return [m for m in runner.received if m.type == "provision"]


async def _create_project(
    workspace: WorkspaceHandle,
    clock: FixedClock,
    name: str,
    *,
    link: str | None = None,
    relay: bool = True,
) -> UUID:
    """A project made through the projects api (it emits `project.created`), relayed to
    its subscribers unless `relay` is False."""
    from tumnis.core.events import relay_once  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415
    from tumnis.modules.projects.api import (  # noqa: PLC0415
        AgentProfileChoice,
        ProjectCreateIn,
    )

    choice = (
        AgentProfileChoice(mode="create")
        if link is None
        else AgentProfileChoice(mode="link", name=link)
    )
    async with tenant_session(workspace.ctx) as s:
        project = await projects.create_project(
            s,
            workspace.ctx.actor,
            ProjectCreateIn(name=name, profile=choice),
            now=clock.now(),
        )
    if relay:
        assert await relay_once() >= 1
    return project.id


def _subscribers() -> None:
    import tumnis.modules.agents.events  # noqa: F401, PLC0415  # registers the subscriber


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P1-06")
async def test_project_created_provisions_once(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-06-01
    Creating a project yields one `provision` mailbox message (create, the bundled
    template and its version) and one `agent_profiles` row, named after the project, that
    ends `ready` on the runner that answered; the project's agent is then `ready`.
    """
    _subscribers()
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.agents.api import (  # noqa: PLC0415
        TEMPLATE_VERSION,
    )

    runner = fake_runner(profiles=[MASTER])
    project_id = await _create_project(workspace, clock, "Acme Site")

    await _settle(_status_is(db, project_id, "ready"))
    assert _profile(db, project_id) == [("acme-site", "ready")]
    assert _provision_messages(db, "acme-site") == 1
    await asyncio.to_thread(runner.wait_for, lambda r: len(_provisions(r)) == 1)
    [provision] = _provisions(runner)
    assert provision.mode == "create"
    assert provision.template == "project-template"
    assert provision.template_version == TEMPLATE_VERSION
    [(runner_id,)] = _owner(
        db, "SELECT runner_id FROM agent_profiles WHERE project_id = %s", (project_id,)
    )
    assert runner_id == runner.runner_id
    async with tenant_session(workspace.ctx):
        status = await agents.agent_for_project(project_id, now=clock.now(), ctx=workspace.ctx)
    assert status == "ready"
    assert _failed_items(db, project_id) == []


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
async def test_duplicate_event_delivery_provisions_once(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-06-02
    The relay's delivery of `project.created` to the agents subscriber runs three more
    times for the same event (a relay retry): still one `provision_profile` workflow, one
    mailbox message, one row, and the runner saw one `provision`.
    """
    _subscribers()
    from dbos import SetWorkflowID  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    from tumnis.core.events import (  # noqa: PLC0415
        EVENTS_QUEUE,
        EventEnvelope,
        deliver_event,
        delivery_id,
    )
    from tumnis.core.outbox import outbox_table  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents.events import (  # noqa: PLC0415
        PROVISION_SUBSCRIBER,
    )

    runner = fake_runner(profiles=[MASTER])
    project_id = await _create_project(workspace, clock, "Acme Site")
    async with tenant_session(workspace.ctx) as s:
        row = (
            (await s.execute(select(outbox_table).where(outbox_table.c.name == "project.created")))
            .mappings()
            .one()
        )
    envelope = EventEnvelope.from_outbox_row(row)
    first: WorkflowHandleAsync[str] = await dbos.retrieve_workflow_async(
        delivery_id(envelope.event_id, PROVISION_SUBSCRIBER)
    )
    assert await first.get_result() == "delivered"
    for n in range(3):
        wf_id = f"{delivery_id(envelope.event_id, PROVISION_SUBSCRIBER)}:again-{n}"
        with SetWorkflowID(wf_id):
            again: WorkflowHandleAsync[str] = await dbos.enqueue_workflow_async(
                EVENTS_QUEUE, deliver_event, PROVISION_SUBSCRIBER, envelope.model_dump(mode="json")
            )
        assert await again.get_result() == "delivered"

    await _settle(_status_is(db, project_id, "ready"))
    await asyncio.sleep(1)  # room for a second provision, were one started
    provisions = await asyncio.to_thread(dbos.list_workflows, name="provision_profile")
    assert [w.workflow_id for w in provisions] == [f"provision:{project_id}"]
    assert _provision_messages(db, "acme-site") == 1
    assert _profile(db, project_id) == [("acme-site", "ready")]
    assert len(_provisions(runner)) == 1


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
@pytest.mark.slow
async def test_killed_worker_resumes_without_second_install(
    worker_killer: WorkerKillerFactory,
    app_factory: AppFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-06-03
    The worker relays `project.created`, starts `provision_profile` and is killed at
    `agents.send_provision_step` right after the mailbox insert committed. The restarted
    worker recovers the workflow, re-runs the step (the deterministic message id writes
    nothing new) and receives the runner's one answer: one mailbox message, one
    `provision` seen by the runner, the profile `ready`.
    """
    from tests.fakes.fake_runner import FakeRunner, create_runner, make_test_client  # noqa: PLC0415
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    killer = worker_killer("agents.send_provision_step", events=0)
    app = app_factory(dbos_system_database_url=killer.sys_db.url(APP))
    app.state.clock = SystemClock()  # the worker's runner sweep runs on the real clock
    runner_id, token = create_runner(workspace, clock, "homelab-hermes")
    with make_test_client(app) as http:
        runner = FakeRunner(
            http, token, runner_id, name="homelab-hermes", profiles=[MASTER], clock=clock
        )
        runner.connect()
        try:
            project_id = await _create_project(workspace, clock, "Acme Site", relay=False)
            assert await killer.run_until_killed(timeout_s=60) == KILLED_EXIT, killer.log_tail()
            assert _provision_messages(db, "acme-site") == 1
            assert _profile(db, project_id) == [("acme-site", "provisioning")]

            worker = await killer.start(None)
            try:
                await _settle(_status_is(db, project_id, "ready"), wait_s=60)
            finally:
                await killer.stop(worker)
        finally:
            runner.disconnect()

    assert _provision_messages(db, "acme-site") == 1
    assert len(_provisions(runner)) == 1, killer.log_tail()
    assert _profile(db, project_id) == [("acme-site", "ready")]
    assert _failed_items(db, project_id) == []


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P1-06")
async def test_link_existing_profile_checks_existence(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-06-04
    Linking to a profile the runner has (`old-site`) makes the project's agent that
    profile, `ready`, with no install; linking to one it lacks leaves the project
    `not_provisioned` and a `provisioning_failed` item with `not_found`.
    """
    _subscribers()
    runner = fake_runner(profiles=[MASTER, "old-site"])
    linked = await _create_project(workspace, clock, "Old Site", link="old-site")
    missing = await _create_project(workspace, clock, "Missing Site", link="missing-site")

    await _settle(_status_is(db, linked, "ready"))
    await _settle(_status_is(db, missing, "not_provisioned"))
    assert _profile(db, linked) == [("old-site", "ready")]
    assert _profile(db, missing) == [("missing-site", "not_provisioned")]
    modes = {(m.profile, m.mode) for m in _provisions(runner)}
    assert modes == {("old-site", "link"), ("missing-site", "link")}
    assert _failed_items(db, linked) == []
    [(payload,)] = _failed_items(db, missing)
    assert payload["profile"] == "missing-site"
    assert payload["mode"] == "link"
    assert payload["error_code"] == "not_found"


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
async def test_master_registry_updated(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-06-05
    After provisioning, `master_registry()` lists the project's agent (project, profile,
    `ready`, its runner), and a planning request built with it carries the entry in its
    `agents` list.
    """
    _subscribers()
    from tumnis.core.tenancy import use_workspace  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.agents.api import (  # noqa: PLC0415
        ProjectAgentEntry,
        master_registry,
    )

    fake_runner(profiles=[MASTER])
    project_id = await _create_project(workspace, clock, "Acme Site")
    await _settle(_status_is(db, project_id, "ready"))

    with use_workspace(workspace.ctx):
        registry = await master_registry()
    entry = ProjectAgentEntry(
        project_id=project_id,
        project_name="Acme Site",
        profile="acme-site",
        status="ready",
        runner="homelab-hermes",
    )
    assert registry == [entry]
    request = agents.PlanningRequest.model_validate(
        {
            "day": "2026-03-09",
            "timezone": "America/New_York",
            "now": "2026-03-09T12:00:00Z",
            "working_window": None,
            "free_blocks": [],
            "candidates": [],
            "projects": [],
            "agents": [e.model_dump(mode="json") for e in registry],
            "events": [],
        }
    )
    assert entry in request.agents


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
async def test_failed_provision_leaves_project_usable(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-06-06
    The runner answers `failed` with `hermes_error`: the project keeps its status and takes
    tasks, its agent is `not_provisioned`, and one `provisioning_failed` item (payload:
    profile, mode, error_code) waits on the project.
    """
    _subscribers()
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    runner = fake_runner(profiles=[MASTER])
    runner.script_provision(profile="acme-site", status="failed", error_code="hermes_error")
    project_id = await _create_project(workspace, clock, "Acme Site")

    await _settle(_status_is(db, project_id, "not_provisioned"))
    await _settle(lambda: len(_failed_items(db, project_id)) == 1)
    [(payload,)] = _failed_items(db, project_id)
    assert payload["profile"] == "acme-site"
    assert payload["mode"] == "create"
    assert payload["error_code"] == "hermes_error"
    [(target_type, target_id)] = _owner(
        db,
        "SELECT target_type, target_id FROM review_items WHERE kind = 'provisioning_failed'",
    )
    assert (target_type, target_id) == ("project", project_id)

    async with tenant_session(workspace.ctx) as s:
        project = await projects.get_project(s, project_id, now=clock.now())
        task = await tasks.create_task(
            s,
            workspace.ctx.actor,
            tasks.TaskCreate(project_id=project_id, title="Send Acme the March invoice"),
            now=clock.now(),
        )
    assert project.status == "active"
    assert task.project_id == project_id
    status = await agents.agent_for_project(project_id, now=clock.now(), ctx=workspace.ctx)
    assert status == "not_provisioned"


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
async def test_timeout_counts_as_failure_and_retry_works(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-06-07
    With the provision timeout set to 1 s (configurable, R-30) and a runner that does not
    answer in time: `not_provisioned` and one `provisioning_failed` item. Retrying
    (`retry_provision`, what accepting the item does) sends a second `provision` that
    succeeds: the profile is `ready`, with no second review item.
    """
    _subscribers()
    from tumnis.core.tenancy import use_workspace  # noqa: PLC0415
    from tumnis.modules.agents.api import (  # noqa: PLC0415
        configure_provisioning,
        provision_timeout_s,
        retry_provision,
    )

    before = provision_timeout_s()
    configure_provisioning(timeout_s=1)
    try:
        runner = fake_runner(profiles=[MASTER])
        runner.script_provision(profile="acme-site", status="created", delay_ms=60_000)
        project_id = await _create_project(workspace, clock, "Acme Site")
        await _settle(_status_is(db, project_id, "not_provisioned"))
        await _settle(lambda: len(_failed_items(db, project_id)) == 1)
        [(payload,)] = _failed_items(db, project_id)
        assert payload["error_code"] == "timeout"

        runner.script_provision(profile="acme-site", status="created")
        with use_workspace(workspace.ctx):
            await retry_provision(project_id)
            await retry_provision(project_id)  # a repeated accept starts nothing new
        await _settle(_status_is(db, project_id, "ready"))
    finally:
        configure_provisioning(timeout_s=before)

    assert _profile(db, project_id) == [("acme-site", "ready")]
    assert len(_failed_items(db, project_id)) == 1
    assert _provision_messages(db, "acme-site") == 2
    provisions = await asyncio.to_thread(dbos.list_workflows, name="provision_profile")
    assert sorted(w.workflow_id for w in provisions) == [
        f"provision:{project_id}",
        f"provision:{project_id}:1",
    ]
