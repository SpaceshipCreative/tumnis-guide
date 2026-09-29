"""SECURITY DEFINER functions run as their owner, which row-level security does not fence,
so each one is a deliberate hole in the tenancy wall (ADR-0009). The list is closed: a new
one fails here until it is added with the reason it needs to cross workspaces (P0-07)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# "<schema>.<function>" -> why it must bypass the tenant policy.
ALLOWED: dict[str, str] = {
    "app.deployment_markers": "P0-06: boot checks read deployment_marker with no table grant",
    "app.outbox_claim": "P0-07: the relay claims unsent outbox rows of every workspace",
    "app.outbox_mark_sent": "P0-07: the relay marks the rows it claimed as sent",
    "app.list_workspace_ids": "P0-15: the audit chain verify walks every workspace",
    "app.provider_setting_keys": (
        "P0-08: the preview boot check sees which provider credential keys any workspace "
        "holds (key names only)"
    ),
    "app.dead_letter_counts": "P0-27: /metrics counts dead letters by status across workspaces",
    "app.usage_totals": "P0-27: /metrics sums usage counters per counter across workspaces",
}

_DEFINERS = """
SELECT n.nspname || '.' || p.proname
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY 1
"""


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-07")
def test_security_definer_functions_are_allowlisted(db: DbUrls) -> None:
    """T-P0-07-16
    `pg_proc.prosecdef` functions equal the allow-list: `app.outbox_claim` and
    `app.outbox_mark_sent` (P0-07) beside P0-06's `app.deployment_markers`; later WPs
    extend it with a reason.
    """
    with psycopg.connect(db.libpq(OWNER)) as conn:
        found = {name for (name,) in conn.execute(_DEFINERS)}
    assert found == set(ALLOWED)
