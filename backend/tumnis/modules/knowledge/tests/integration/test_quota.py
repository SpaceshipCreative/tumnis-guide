"""The workspace knowledge quota (P1-17, FR-15.6): bytes used against the
`knowledge.quota_bytes` setting (10 GiB by default)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests._samples import fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import settled, upload

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

GIB = 1024**3


@pytest.mark.req("FR-15.6")
@pytest.mark.wp("P1-17")
@pytest.mark.xfail(strict=True, reason="spec:P1-17")
async def test_quota_computed(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient
) -> None:
    """T-P1-17-14
    `GET /v1/knowledge/quota` answers the workspace's used bytes against its quota
    (10 GiB by default): an extracted upload adds its file's size, a text entry its body's
    UTF-8 size; a trashed entry still counts until the trash is purged, then it does not.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    env = extract_env

    async def used() -> int:
        got = await session_client.get("/v1/knowledge/quota")
        assert got.status_code == 200, got.text
        assert got.json()["quota_bytes"] == 10 * GIB
        value: int = got.json()["used_bytes"]
        return value

    before = await used()
    data = fixture_bytes("rate-card-table.pdf")
    accepted = await upload(session_client, env.project_id, "rate-card-table.pdf", data)
    assert accepted.status_code == 202, accepted.text
    await settled(session_client, accepted.json()["id"])
    assert await used() == before + len(data)

    body = "Rates agreed with Dana - 160 € an hour."
    made = await session_client.post(
        "/v1/knowledge/documents/text",
        json={"project_id": str(env.project_id), "title": "Rates", "body_md": body},
    )
    assert made.status_code == 201, made.text
    assert await used() == before + len(data) + len(body.encode())

    trashed = await session_client.delete(f"/v1/knowledge/documents/{made.json()['id']}")
    assert trashed.status_code == 204, trashed.text
    assert await used() == before + len(data) + len(body.encode())

    async with tenant_session(env.ws.ctx) as s:
        purged = await knowledge.purge_trash(s, datetime(9999, 1, 1, tzinfo=UTC), limit=100)
    assert purged == 1
    assert await used() == before + len(data)
