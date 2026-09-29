"""The seed set loads projects and their briefs through the api (P0-17, P0-02): board
order from the seed's sort keys, a default policy each, one pinned brief per project."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tumnis.seed import SeedResult

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-2.1", "FR-2.3")
@pytest.mark.wp("P0-17")
async def test_seed_loads_projects_and_briefs(db: DbUrls, seed: SeedResult) -> None:
    workspace = seed.ids["ws_main"]
    with psycopg.connect(db.libpq(OWNER)) as conn:
        projects = conn.execute(
            "SELECT name, sort_key FROM projects WHERE workspace_id = %s ORDER BY sort_key",
            (workspace,),
        ).fetchall()
        policies = conn.execute(
            "SELECT count(*) FROM project_policies WHERE workspace_id = %s", (workspace,)
        ).fetchone()
        briefs = conn.execute(
            "SELECT p.name, d.pinned, d.trust, d.body_md <> '' FROM documents d"
            " JOIN projects p ON p.id = d.project_id"
            " WHERE d.workspace_id = %s AND d.role = 'brief' ORDER BY p.sort_key",
            (workspace,),
        ).fetchall()
    assert projects == [
        ("Acme brand refresh", "a0"),
        ("Authenticity course", "a1"),
        ("Tumnis dogfood", "a2"),
    ]
    assert policies == (3,)
    assert briefs == [(name, True, "trusted", True) for name, _ in projects]
