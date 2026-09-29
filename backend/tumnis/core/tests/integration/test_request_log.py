"""`GET /v1/test/requests` (fakes only) lists recent writes and whether they replayed (P0-10,
read by A0.2); `redact_on_replay` keeps a shown-once field out of the stored response."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tumnis.core.tests.integration._demo import Demo

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
async def test_recent_writes_are_listed_with_replays(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """Two POSTs with one key: the log lists both, the second as replayed; reads are not
    listed."""
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import principal_header  # noqa: PLC0415

    who = principal_header(make_workspace(db), uuid.uuid4())
    headers = {**who, "Idempotency-Key": "request-log-01"}
    for _ in range(2):
        response = await demo_client.post("/v1/demo-items", json={"title": "a"}, headers=headers)
        assert response.status_code == 201, response.text
    await demo_client.get("/v1/demo-items", headers=who)

    log = await demo_client.get("/v1/test/requests")
    assert log.status_code == 200, log.text
    items = log.json()["items"]
    assert [(i["method"], i["route"], i["idempotency_key"], i["replayed"]) for i in items] == [
        ("POST", "/v1/demo-items", "request-log-01", False),
        ("POST", "/v1/demo-items", "request-log-01", True),
    ]


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
def test_redacted_fields_are_not_stored_for_replay() -> None:
    """The stored body drops the fields named in `redact_on_replay` and says which."""
    import json  # noqa: PLC0415

    from tumnis.core.idempotency import _redacted_body  # noqa: PLC0415

    body = json.dumps({"id": "k1", "secret": "tmk_abc", "name": "ci"}).encode()
    stored = json.loads(_redacted_body(body, ("secret",)))
    assert stored == {"id": "k1", "name": "ci", "redacted": ["secret"]}
    assert _redacted_body(body, ()) == body
