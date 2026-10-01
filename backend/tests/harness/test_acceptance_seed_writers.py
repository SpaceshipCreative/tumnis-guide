"""The acceptance set through DatabaseSink (Scott decision 37): the runner, the agents with
their keys and the tainted email link reach their modules' tables.

Project agents are written `ready` (provisioned), so the `project.created` relay of the
seed's own projects leaves them alone (P1-06's provision ends at a ready profile); each
agent's key is generated at load time and linked as the key its runs issue task tokens
from (P2-02), never read from the repository.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import httpx

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
    result = await _load_set(SEED_PATHS[SeedSet("acceptance")], db, clock)

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


@pytest.mark.xfail(strict=True, reason="spec:SEED")
async def test_reset_loads_the_acceptance_set_at_an_anchor(
    client: httpx.AsyncClient, db: DbUrls
) -> None:
    """T-SEED-13
    `POST /v1/test/reset?set=acceptance&anchor=2026-03-09` (what the e2e journeys send)
    loads the acceptance set with its dates on that Monday, whatever the server's day:
    `Write Acme proposal` is due then and Monday's first busy event starts at 09:00 New York
    time; a bad anchor is 422."""
    monday = {"set": "acceptance", "anchor": "2026-03-09"}
    reset = await client.post("/v1/test/reset", params=monday)
    assert reset.status_code == 204, reset.text

    names = _rows(db, "SELECT name FROM projects WHERE deleted_at IS NULL ORDER BY sort_key")
    assert [p["name"] for p in names] == ["Acme site", "Beta app", "Gamma ops"]
    assert _rows(db, "SELECT due_on FROM tasks WHERE title = 'Write Acme proposal'") == [
        {"due_on": date(2026, 3, 9)}
    ]
    first = _rows(
        db,
        "SELECT to_char(min(start_at) AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI')"
        " AS at FROM events",
    )
    assert first == [{"at": "2026-03-09 09:00"}]
    agents = _rows(db, "SELECT count(*) AS n FROM agent_profiles WHERE deleted_at IS NULL")
    assert agents == [{"n": 4}]
    bad = await client.post("/v1/test/reset", params={"set": "acceptance", "anchor": "Monday"})
    assert bad.status_code == 422
