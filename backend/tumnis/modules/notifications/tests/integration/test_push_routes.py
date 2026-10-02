"""`DELETE /v1/push/subscriptions/{push_subscription_id}` (P4-05, FR-8.3): a user forgets
only their own browsers' subscriptions. Another member's subscription in the same
workspace answers 404, as an unknown one does, and stays live."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tumnis.modules.notifications.tests.integration._push import browser_keys, rows

if TYPE_CHECKING:
    from tumnis.modules.notifications.tests.integration._push import PushWorld

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-8.3")
@pytest.mark.wp("P4-05")
async def test_unsubscribe_only_own_subscription(push: PushWorld) -> None:
    own = await push.subscribe()
    assert own.status_code == 201
    keys = browser_keys()
    [other] = rows(
        push.db,
        "INSERT INTO push_subscriptions (workspace_id, user_id, endpoint, p256dh, auth)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        push.workspace.id,
        uuid4(),  # another member of the workspace
        f"https://fcm.googleapis.com/fcm/send/{uuid4()}",
        keys["p256dh"],
        keys["auth"],
    )

    refused = await push.http.delete(f"/v1/push/subscriptions/{other['id']}")
    assert refused.status_code == 404
    assert other["id"] in {s["id"] for s in push.subscriptions()}

    done = await push.http.delete(f"/v1/push/subscriptions/{own.json()['id']}")
    assert done.status_code == 204
    assert {s["id"] for s in push.subscriptions()} == {other["id"]}
