"""A provision's last step against a profile the seed adopted meanwhile (P1-06 with SEED;
CodeRabbit on #158). `seed_agent` takes over the row a provision wrote (APP-04) and
cancels the provision, but DBOS stops a workflow only at its next step: a
`finish_provision_step` already running still finishes. It changes the profile only while
the profile is in the state the provision left it in, so an adopted (`ready`, renamed)
profile keeps its status, gets no `provisioning_failed` item and no inventory entry under
the provision's old name."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import PepperFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.req("FR-5.10"),
    pytest.mark.wp("P1-06"),
    pytest.mark.integration,
    pytest.mark.enable_socket,
]


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


async def _project(workspace: WorkspaceHandle, clock: FixedClock, name: str) -> UUID:
    """A project made through the projects api; its `project.created` is never relayed."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415
    from tumnis.modules.projects.api import (  # noqa: PLC0415
        AgentProfileChoice,
        ProjectCreateIn,
    )

    async with tenant_session(workspace.ctx) as s:
        project = await projects.create_project(
            s,
            workspace.ctx.actor,
            ProjectCreateIn(name=name, profile=AgentProfileChoice(mode="create")),
            now=clock.now(),
        )
    return project.id


def _adopted(db: DbUrls, project_id: UUID, runner_id: UUID | None) -> UUID:
    """The row `choose_name_step` wrote (`acme-site`, `provisioning`), then taken over by
    the seed: renamed, on the seed's runner, `ready`."""
    [(profile_id,)] = _owner(
        db,
        "INSERT INTO agent_profiles (workspace_id, name, role, project_id, transport, status,"
        " provision_mode) SELECT workspace_id, 'acme-site', 'project', id, 'daemon',"
        " 'provisioning', 'create' FROM projects WHERE id = %s RETURNING id",
        (project_id,),
    )
    _owner(
        db,
        "UPDATE agent_profiles SET name = 'acme-seed', runner_id = %s, status = 'ready'"
        " WHERE id = %s RETURNING id",
        (runner_id, profile_id),
    )
    return UUID(str(profile_id))


def _chosen(profile_id: UUID) -> dict[str, Any]:
    return {"profile_id": str(profile_id), "name": "acme-site", "mode": "create"}


def _failed_items(db: DbUrls, project_id: UUID) -> int:
    [(count,)] = _owner(
        db,
        "SELECT count(*) FROM review_items WHERE kind = 'provisioning_failed'"
        " AND project_id = %s AND deleted_at IS NULL",
        (project_id,),
    )
    return int(count)


async def test_a_failed_provision_leaves_an_adopted_profile_alone(
    dbos: type[DBOS], workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls
) -> None:
    """The provision fails (no runner) after the seed adopted its profile: the profile stays
    `ready` under the seed's name, and no `provisioning_failed` item is added."""
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    project_id = await _project(workspace, clock, "Acme site")
    profile_id = _adopted(db, project_id, None)

    await workflows.finish_provision_step(
        str(workspace.id), str(project_id), _chosen(profile_id), 0, None, "no_runner"
    )

    assert _owner(db, "SELECT name, status FROM agent_profiles WHERE id = %s", (profile_id,)) == [
        ("acme-seed", "ready")
    ]
    assert _failed_items(db, project_id) == 0


async def test_a_ready_provision_leaves_an_adopted_profile_alone(
    dbos: type[DBOS],
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    pepper_file: PepperFile,  # the runner's device token is an HMAC with the pepper
) -> None:
    """The runner answers `created` after the seed adopted the profile: the profile keeps
    the seed's state (no profile version written) and its runner's inventory gets no entry
    under the provision's old name."""
    from tests.fakes.fake_runner import create_runner  # noqa: PLC0415
    from tumnis.modules.agents import workflows  # noqa: PLC0415
    from tumnis.modules.agents.protocol import ProvisionResult  # noqa: PLC0415

    project_id = await _project(workspace, clock, "Acme site")
    runner_id, _token = create_runner(workspace, clock, "homelab")
    profile_id = _adopted(db, project_id, runner_id)
    reply = ProvisionResult(
        message_id=uuid.uuid4(),
        correlation_id="provision-test",
        sent_at=clock.now(),
        request_id=uuid.uuid4(),
        profile="acme-site",
        status="created",
        distribution_version="1.2.3",
        error_code=None,
    ).model_dump(mode="json")

    await workflows.finish_provision_step(
        str(workspace.id), str(project_id), _chosen(profile_id), 0, reply, None
    )

    assert _owner(
        db, "SELECT name, status, profile_version FROM agent_profiles WHERE id = %s", (profile_id,)
    ) == [("acme-seed", "ready", None)]
    [(inventory,)] = _owner(db, "SELECT inventory FROM runners WHERE id = %s", (runner_id,))
    assert all(entry.get("name") != "acme-site" for entry in inventory or [])


async def test_sending_a_provision_leaves_an_adopted_profile_alone(
    dbos: type[DBOS],
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    pepper_file: PepperFile,  # the runners' device tokens are HMACs with the pepper
) -> None:
    """The provision's send step runs after the seed adopted the profile: the profile keeps
    the seed's runner, no `provision` message is queued under the old name, and the step
    says nothing was sent."""
    from tests.fakes.fake_runner import create_runner  # noqa: PLC0415
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    project_id = await _project(workspace, clock, "Acme site")
    seed_runner, _token = create_runner(workspace, clock, "homelab")
    other_runner, _other = create_runner(workspace, clock, "spare")
    profile_id = _adopted(db, project_id, seed_runner)

    sent = await workflows.send_provision_step(
        str(workspace.id),
        f"provision:{project_id}",
        str(profile_id),
        str(other_runner),
        "acme-site",
        "create",
    )

    assert sent is False
    assert _owner(db, "SELECT runner_id FROM agent_profiles WHERE id = %s", (profile_id,)) == [
        (seed_runner,)
    ]
    assert _owner(db, "SELECT count(*) FROM runner_messages WHERE type = 'provision'", ()) == [(0,)]
