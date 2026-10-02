"""A fake-only provider can't be connected outside fakes mode (P3-02 follow-up, CodeRabbit on
#156): `GET /v1/connections/providers` already hides provider `fake` unless
TUMNIS_ADAPTERS=fake, and `POST /v1/connections` refuses it the same way."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.tests.integration._connections import rows

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
async def test_fake_only_provider_refused_outside_fakes_mode(
    session_client: SessionClient, db: DbUrls, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With real adapters, creating a connection of the fake-only provider `fake` answers
    422 `unknown_provider` and stores no connection; with fakes it is created."""
    monkeypatch.setenv("TUMNIS_ADAPTERS", "real")
    refused = await session_client.post(
        "/v1/connections", json={"provider": "fake", "account_label": "Work"}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "unknown_provider"
    assert rows(db, "SELECT id FROM connections WHERE provider = 'fake'") == []

    monkeypatch.setenv("TUMNIS_ADAPTERS", "fake")
    made = await session_client.post(
        "/v1/connections", json={"provider": "fake", "account_label": "Work"}
    )
    assert made.status_code == 201, made.text
