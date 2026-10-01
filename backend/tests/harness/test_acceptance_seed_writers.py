"""The acceptance set through DatabaseSink (Scott decision 37): the runner, the agents with
their keys and the tainted email link reach their modules' tables.

Project agents are written `ready` (provisioned), so the `project.created` relay of the
seed's own projects leaves them alone (P1-06's provision ends at a ready profile); each
agent's key is generated at load time and linked as the key its runs issue task tokens
from (P2-02), never read from the repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, PepperFile
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.req("A1.4", "A2.1"),
    pytest.mark.wp("SEED"),
]


def _rows(db: DbUrls, query: str) -> list[dict[str, Any]]:
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query.encode()).fetchall())


@pytest.mark.xfail(strict=True, reason="spec:SEED")
async def test_acceptance_set_writes_runner_agents_keys_and_link(
    db: DbUrls, clock: FixedClock, master_key_file: MasterKeyFile, pepper_file: PepperFile
) -> None:
    """T-SEED-10
    Loaded into Postgres, the acceptance set has the runner `homelab-hermes`; the master
    and the three project agents on it (project agents `ready`), each linked to a live key
    with its scopes; and `Fix footer link` with its acceptance criteria, linked to the email,
    tainted like the link."""
    from tests.fixtures import _load_set  # noqa: PLC0415
    from tumnis.seed import SEED_PATHS, SeedSet  # noqa: PLC0415

    del master_key_file, pepper_file  # the user's TOTP secret and the keys need them
    result = await _load_set(SEED_PATHS[SeedSet("acceptance")], db, clock)  # type: ignore[arg-type]

    assert result.counts["agent"] == 4
    assert [r["name"] for r in _rows(db, "SELECT name FROM runners")] == ["homelab-hermes"]
    agents = _rows(
        db,
        "SELECT a.name, a.role, a.status, a.transport, p.name AS project, r.name AS runner,"
        " k.scopes, k.revoked_at FROM agent_profiles a"
        " LEFT JOIN projects p ON p.id = a.project_id"
        " JOIN runners r ON r.id = a.runner_id"
        " JOIN api_keys k ON k.id = a.api_key_id"
        " WHERE a.deleted_at IS NULL ORDER BY a.name",
    )
    assert [(a["name"], a["role"], a["project"]) for a in agents] == [
        ("acme-site", "project", "Acme site"),
        ("beta-app", "project", "Beta app"),
        ("gamma-ops", "project", "Gamma ops"),
        ("tumnis-master", "master", None),
    ]
    for agent in agents:
        assert agent["runner"] == "homelab-hermes"
        assert agent["transport"] == "daemon"
        assert agent["revoked_at"] is None
        if agent["role"] == "project":
            assert agent["status"] == "ready"
            assert set(agent["scopes"]) == {"tasks:read", "tasks:write", "context:read"}
        else:
            assert set(agent["scopes"]) == {"tasks:read", "tasks:write", "delegate"}

    [footer] = _rows(
        db,
        "SELECT id, tainted, acceptance_criteria FROM tasks WHERE title = 'Fix footer link'",
    )
    assert footer["acceptance_criteria"]
    assert footer["tainted"] is True
    links = _rows(
        db,
        "SELECT owner_type, owner_id, target_type, target_url, tainted FROM context_items"
        " WHERE deleted_at IS NULL",
    )
    assert links == [
        {
            "owner_type": "task",
            "owner_id": footer["id"],
            "target_type": "url",
            "target_url": "https://mail.example.com/acme/threads/footer-link",
            "tainted": True,
        }
    ]
    assert _rows(db, "SELECT count(*) AS n FROM tasks WHERE status = 'today'") == [{"n": 0}]
