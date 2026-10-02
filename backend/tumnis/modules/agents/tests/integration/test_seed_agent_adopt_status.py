"""The seed adopts only a profile a provision is still making (SEED with P1-06;
CodeRabbit on #158). A registration conflict on the project's own live profile used to be
adopted whatever its status, so a `registered` or `ready` agent lost its name and runner
to the seed. Only a `provisioning` row is the worker's provision; any other keeps the
registration conflict."""

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


@pytest.mark.parametrize("status", ["registered", "ready", "paused"])
@pytest.mark.parametrize("name", ["acme-site", "acme-live"])
async def test_seed_agent_keeps_the_conflict_for_a_live_agent(
    session_client: SessionClient,
    db: DbUrls,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    name: str,
) -> None:
    """The project already has its agent (`registered` or `ready`; under the seed's name:
    `profile_exists`, or another: `project_agent_exists`). The seed agent is refused with
    that conflict, the agent keeps its name, runner and status, and nothing is cancelled."""
    from tumnis.core import deadletter  # noqa: PLC0415
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.seed import AgentSeed  # noqa: PLC0415

    client = _Client()
    monkeypatch.setattr(deadletter, "dbos_configured", lambda: True)
    monkeypatch.setattr(deadletter, "dbos_client", lambda: client)
    assert session_client.account is not None
    workspace_id = session_client.account.workspace_id
    made = await session_client.post("/v1/projects", json={"name": "Acme site"})
    assert made.status_code == 201, made.text
    project_id = UUID(made.json()["id"])
    [(live,)] = _owner(
        db,
        "INSERT INTO agent_profiles (workspace_id, name, role, project_id, transport, status,"
        " provision_mode) SELECT workspace_id, %s, 'project', id, 'daemon', %s, 'create'"
        " FROM projects WHERE id = %s RETURNING id",
        (name, status, project_id),
    )

    with pytest.raises(ProblemError) as refused:
        await agents.seed_agent(
            workspace_id,
            None,
            project_id,
            AgentSeed(key="ag", name="acme-site", role="project", key_scopes=()),
        )

    expected = "profile_exists" if name == "acme-site" else "project_agent_exists"
    assert refused.value.problem.code == expected
    assert _owner(
        db,
        "SELECT id, name, status, transport FROM agent_profiles"
        " WHERE project_id = %s AND deleted_at IS NULL",
        (project_id,),
    ) == [(live, name, status, "daemon")]
    assert client.cancelled == []


async def test_seed_agent_adopts_a_provision_that_ended_without_a_runner(
    session_client: SessionClient, db: DbUrls, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed writes its projects before its runners, so the project's provision can end
    `not_provisioned` (`no_runner`) before the seed agent registers: that row is still the
    provision's, and the seed takes it over, ready, as it does a `provisioning` one."""
    from tumnis.core import deadletter  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.seed import AgentSeed  # noqa: PLC0415

    client = _Client()
    monkeypatch.setattr(deadletter, "dbos_configured", lambda: True)
    monkeypatch.setattr(deadletter, "dbos_client", lambda: client)
    assert session_client.account is not None
    workspace_id = session_client.account.workspace_id
    made = await session_client.post("/v1/projects", json={"name": "Acme site"})
    assert made.status_code == 201, made.text
    project_id = UUID(made.json()["id"])
    [(provisioned,)] = _owner(
        db,
        "INSERT INTO agent_profiles (workspace_id, name, role, project_id, transport, status,"
        " provision_mode) SELECT workspace_id, 'acme-site', 'project', id, 'daemon',"
        " 'not_provisioned', 'create' FROM projects WHERE id = %s RETURNING id",
        (project_id,),
    )

    profile_id = await agents.seed_agent(
        workspace_id,
        None,
        project_id,
        AgentSeed(key="ag", name="acme-site", role="project", key_scopes=()),
    )

    assert profile_id == provisioned
    assert _owner(
        db,
        "SELECT name, status FROM agent_profiles WHERE project_id = %s AND deleted_at IS NULL",
        (project_id,),
    ) == [("acme-site", "ready")]
    assert client.cancelled == [f"provision:{project_id}"]
