"""ETags and 304s on GET (P0-22, PERF-2): an unchanged read costs a few bytes on the phone
link. Driven through the demo router (`_demo.py`), a detail and a list, until the projects
routes exist (P0-17)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tumnis.core.tests.integration._demo import Demo

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("PERF-2")
@pytest.mark.wp("P0-22")
async def test_unchanged_get_returns_304(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-22-12
    A GET answers 200 with a strong `ETag` and `Cache-Control: private, no-cache`; the same
    GET with `If-None-Match` answers 304 with the tag and no body; after a PATCH the old
    tag gets 200 and a new tag. Same for the list.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import principal_header  # noqa: PLC0415

    who = principal_header(make_workspace(db), uuid.uuid4())

    def key() -> dict[str, str]:
        return {**who, "Idempotency-Key": f"etag-{uuid.uuid4()}"}

    created = await demo_client.post("/v1/demo-items", json={"title": "a"}, headers=key())
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    for path in (f"/v1/demo-items/{item_id}", "/v1/demo-items"):
        first = await demo_client.get(path, headers=who)
        assert first.status_code == 200, first.text
        tag = first.headers["etag"]
        assert tag.startswith('"')
        assert tag.endswith('"')
        assert first.headers["cache-control"] == "private, no-cache"

        again = await demo_client.get(path, headers={**who, "If-None-Match": tag})
        assert again.status_code == 304
        assert again.content == b""
        assert again.headers["etag"] == tag
        # Weak comparison and a list of tags match too.
        weak = await demo_client.get(path, headers={**who, "If-None-Match": f'"x", W/{tag}'})
        assert weak.status_code == 304

        version = (await demo_client.get(f"/v1/demo-items/{item_id}", headers=who)).json()[
            "version"
        ]
        patched = await demo_client.patch(
            f"/v1/demo-items/{item_id}",
            json={"title": f"renamed {version}", "version": version},
            headers=key(),
        )
        assert patched.status_code == 200, patched.text

        changed = await demo_client.get(path, headers={**who, "If-None-Match": tag})
        assert changed.status_code == 200
        assert changed.headers["etag"] != tag
        assert changed.json()

    # Errors and writes carry no tag.
    missing = await demo_client.get(f"/v1/demo-items/{uuid.uuid4()}", headers=who)
    assert missing.status_code == 404
    assert "etag" not in missing.headers
    assert "etag" not in created.headers
