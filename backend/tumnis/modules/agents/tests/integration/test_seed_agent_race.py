"""The acceptance seed's project agents against the worker's provisioning (SEED, P1-06;
APP-04). `POST /v1/test/reset` answered 409 when the worker's `provision_project` (from
the seed's own `project.created`) wrote the project's profile before the seed's
`seed_agent` registered it: `profile_exists` or `project_agent_exists` ended the reset.
The seed now adopts the row the provision made and cancels that provision."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls

pytestmark = [
    pytest.mark.req("REL-7"),
    pytest.mark.wp("SEED"),
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


class _Client:
    """Stands in for the api's DBOS client: records the workflows it is asked to cancel."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel_workflows_async(self, ids: list[str]) -> None:
        self.cancelled.extend(ids)


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


async def _project(client: SessionClient, name: str) -> UUID:
    made = await client.post("/v1/projects", json={"name": name})
    assert made.status_code == 201, made.text
    return UUID(made.json()["id"])


def _provisioned(db: DbUrls, project_id: UUID, name: str) -> UUID:
    """The row `choose_name_step` writes for a new project: named after it, `provisioning`."""
    [(profile_id,)] = _owner(
        db,
        "INSERT INTO agent_profiles (workspace_id, name, role, project_id, transport, status,"
        " provision_mode) SELECT workspace_id, %s, 'project', id, 'daemon', 'provisioning',"
        " 'create' FROM projects WHERE id = %s RETURNING id",
        (name, project_id),
    )
    return UUID(str(profile_id))


@pytest.mark.parametrize("provision_name", ["acme-site", "acme-site-2"])
async def test_seed_agent_adopts_the_profile_a_provision_wrote_first(
    session_client: SessionClient,
    db: DbUrls,
    monkeypatch: pytest.MonkeyPatch,
    provision_name: str,
) -> None:
    """APP-04: the provision got there first (same name: `profile_exists`; another name:
    `project_agent_exists`). The seed agent takes that row over, ready and on the seed's
    runner and name, and the project's provision is cancelled; no second profile."""
    from tumnis.core import deadletter  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.seed import AgentSeed, RunnerSeed  # noqa: PLC0415

    client = _Client()
    monkeypatch.setattr(deadletter, "dbos_configured", lambda: True)
    monkeypatch.setattr(deadletter, "dbos_client", lambda: client)
    assert session_client.account is not None
    workspace_id = session_client.account.workspace_id
    project_id = await _project(session_client, "Acme site")
    provisioned = _provisioned(db, project_id, provision_name)
    runner_id = await agents.seed_runner(workspace_id, RunnerSeed(key="r", name="homelab"))

    profile_id = await agents.seed_agent(
        workspace_id,
        runner_id,
        project_id,
        AgentSeed(key="ag", name="acme-site", role="project", key_scopes=("tasks:read",)),
    )

    assert profile_id == provisioned
    rows = _owner(
        db,
        "SELECT id, name, status, runner_id, transport, api_key_id IS NOT NULL"
        " FROM agent_profiles WHERE project_id = %s AND deleted_at IS NULL",
        (project_id,),
    )
    assert rows == [(provisioned, "acme-site", "ready", runner_id, "daemon", True)]
    assert client.cancelled == [f"provision:{project_id}"]


@pytest.mark.parametrize("own_provision", [False, True])
async def test_seed_agent_still_refuses_a_name_another_project_holds(
    session_client: SessionClient, db: DbUrls, own_provision: bool
) -> None:
    """Only the project's own provision row is adopted, and only for a name it may take: a
    name another project's agent holds is still 409 `profile_exists`, whether or not this
    project's provision has written a row of its own (then under another name)."""
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.seed import AgentSeed  # noqa: PLC0415

    assert session_client.account is not None
    workspace_id = session_client.account.workspace_id
    other = await _project(session_client, "Beta app")
    _provisioned(db, other, "acme-site")
    project_id = await _project(session_client, "Acme site")
    if own_provision:
        _provisioned(db, project_id, "acme-site-2")

    with pytest.raises(ProblemError) as refused:
        await agents.seed_agent(
            workspace_id,
            None,
            project_id,
            AgentSeed(key="ag", name="acme-site", role="project", key_scopes=()),
        )
    assert refused.value.problem.code == "profile_exists"
