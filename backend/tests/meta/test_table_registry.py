"""Table registry: every table in `public` is either a fenced tenant table or on the closed
allow-list in `_catalog.py` (P0-06, ADR-0009). Catalog-driven: a table a later migration
adds is checked here without anyone listing it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _violations(db: DbUrls, kind: str) -> list[str]:
    from tests.meta._catalog import registry_violations  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER)) as conn:
        return registry_violations(conn, kinds=(kind,))


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-06")
def test_every_tenant_table_has_base_columns(db: DbUrls) -> None:
    """T-P0-06-01
    Each non-allow-listed `public` table has the 7 base columns with the right types,
    defaults and nullability (and `created_by` carries ACTOR_CHECK, R-01).
    """
    from tests.meta._catalog import tenant_tables  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER)) as conn:
        assert tenant_tables(conn), "no tenant table to check: the registry would pass vacuously"
    assert _violations(db, "columns") == []


@pytest.mark.req("PERF-1", "ADR-0009")
@pytest.mark.wp("P0-06")
def test_workspace_id_leads_every_composite_index_and_unique_key(db: DbUrls) -> None:
    """T-P0-06-02
    For each index that is unique or multi-column (not the primary key), the first key
    column is `workspace_id`.
    """
    assert _violations(db, "indexes") == []


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-06")
def test_every_tenant_table_has_rls_and_policy(db: DbUrls) -> None:
    """T-P0-06-03
    `relrowsecurity` is true and a `tenant_isolation` policy for `tumnis_app` has both
    USING and WITH CHECK, on every tenant table and on the tenant root `workspaces`.
    """
    assert _violations(db, "rls") == []


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-06")
def test_registry_flags_an_unfenced_table(db: DbUrls) -> None:
    """T-P0-06-04
    A table created ad hoc without the helper is reported by `registry_violations()`, so
    new tables are covered automatically.
    """
    from tests.meta._catalog import registry_violations  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        assert registry_violations(conn) == []
        conn.execute(
            "CREATE TABLE adhoc_unfenced (id uuid PRIMARY KEY, workspace_id uuid, note text)"
        )
        conn.execute("CREATE UNIQUE INDEX adhoc_unfenced_note ON adhoc_unfenced (note)")
        found = registry_violations(conn)

    flagged = [v for v in found if "adhoc_unfenced" in v]
    assert {v.split(":", 1)[0] for v in flagged} == {"columns", "indexes", "rls"}, found
    assert all("adhoc_unfenced" in v for v in found), found


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-06")
def test_allow_list_names_only_existing_tables(db: DbUrls) -> None:
    """The allow-list is closed: an entry whose table does not exist (and is not marked as
    arriving with a later work package) fails, so a stale entry cannot hide a new table."""
    from tests.meta._catalog import stale_allow_list_entries  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER)) as conn:
        assert stale_allow_list_entries(conn) == []
