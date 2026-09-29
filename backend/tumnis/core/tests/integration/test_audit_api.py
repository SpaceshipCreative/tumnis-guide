"""`GET /v1/audit` and `GET /v1/audit.csv` (P0-15, SEC-3)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ACTIONS = ("key.created", "key.revoked", "auth.login")
ONE_MINUTE = timedelta(minutes=1)


async def _all_pages(client: httpx.AsyncClient, **params: Any) -> list[list[dict[str, Any]]]:
    pages: list[list[dict[str, Any]]] = []
    cursor = None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get("/v1/audit", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        pages.append(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
async def test_audit_list_paginates_and_is_workspace_scoped(
    app: FastAPI, db: DbUrls, clock: FixedClock
) -> None:
    """T-P0-15-11
    Seven rows in A a minute apart, two in B. As A, `GET /v1/audit?limit=3` pages newest
    first (3, 3, 1) with every row once; `action`, `actor_type`, `from` and `to` filter;
    B's list holds only B's rows and none of A's ids.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import signed_in, write_rows  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR, ActorRef  # noqa: PLC0415

    a, b = make_workspace(db, "A"), make_workspace(db, "B")
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    start = clock.now()
    await write_rows(WorkspaceContext(a, SYSTEM_ACTOR), 4, clock, actions=ACTIONS)
    await write_rows(WorkspaceContext(a, ActorRef(f"user:{user_a}")), 3, clock, actions=ACTIONS)
    await write_rows(WorkspaceContext(b, SYSTEM_ACTOR), 2, clock)

    async with signed_in(app, a, user_a) as as_a, signed_in(app, b, user_b) as as_b:
        pages = await _all_pages(as_a, limit=3)
        assert [len(page) for page in pages] == [3, 3, 1]
        items = [item for page in pages for item in page]
        assert [item["seq"] for item in items] == [7, 6, 5, 4, 3, 2, 1]
        assert {item["action"] for item in items} == set(ACTIONS)

        revoked = [i for page in await _all_pages(as_a, action="key.revoked") for i in page]
        assert [item["seq"] for item in revoked] == [5, 2]

        by_user = [i for page in await _all_pages(as_a, actor_type="user") for i in page]
        assert [item["seq"] for item in by_user] == [7, 6, 5]
        assert {item["actor_id"] for item in by_user} == {str(user_a)}

        window = {
            "from": (start + 2 * ONE_MINUTE).isoformat(),
            "to": (start + 5 * ONE_MINUTE).isoformat(),
        }
        ranged = [i for page in await _all_pages(as_a, **window) for i in page]
        assert [item["seq"] for item in ranged] == [5, 4, 3]

        b_items = [i for page in await _all_pages(as_b) for i in page]
        assert [item["seq"] for item in b_items] == [2, 1]
        assert not {item["id"] for item in b_items} & {item["id"] for item in items}


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
async def test_csv_export_escapes_formulas(app: FastAPI, db: DbUrls, clock: FixedClock) -> None:
    """T-P0-15-12
    An export whose `User-Agent` is `=HYPERLINK(...)` is itself audited as
    `audit.exported`, and in the CSV that row's `user_agent` cell reads `'=HYPERLINK(...)`,
    so a spreadsheet shows it as text.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import owner_rows, signed_in  # noqa: PLC0415

    formula = '=HYPERLINK("https://attacker.example/","open me")'
    workspace = make_workspace(db)
    async with signed_in(app, workspace, uuid.uuid4(), **{"User-Agent": formula}) as client:
        response = await client.get("/v1/audit.csv")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    exported = [row for row in rows if row["action"] == "audit.exported"]
    assert len(exported) == 1
    assert exported[0]["user_agent"] == "'" + formula
    assert owner_rows(db, "SELECT count(*) FROM audit_log WHERE action = 'audit.exported'") == [
        (1,)
    ]
