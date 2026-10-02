"""Overnight results batch into the morning review (P4-04, J7, FR-8.4): an unattended run's
result and the window's refusals are review items marked `batch: "overnight"` with their
release time. They update the in-app badge at once (FR-8.1) but reach no other channel
during the night; at the release time (the first working hour minus 15 minutes) one summary
goes out, on Discord through the master and as one browser push."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.notifications.tests.integration._overnight import (
    RELEASE_AT,
    notifications,
    overnight_refusal,
    overnight_result,
    release,
)

if TYPE_CHECKING:
    from tumnis.modules.notifications.tests.integration._delivery import DeliveryWorld

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


@pytest.mark.req("J7", "FR-8.4")
@pytest.mark.wp("P4-04")
@pytest.mark.xfail(strict=True, reason="spec:P4-04")
async def test_results_batch_into_morning_review(delivery: DeliveryWorld) -> None:
    """T-P4-04-09
    At Nudge (which sends everything at once), an overnight result and an overnight refusal
    dispatch no notify run and no push, and stay held through a natural break and a release
    tick before their time; at the release time exactly one summary goes out ("1 result
    from overnight"), one notify run and one push; a later tick sends nothing more. The
    result's review item carries `batch: "overnight"` in the queue.
    """
    delivery.master_says("1 result from overnight.")
    await delivery.subscribe()
    green = await delivery.task("Fix footer link")
    tainted = await delivery.task("Reply to the client's request")
    await delivery.level("nudge")

    result = await overnight_result(delivery, green)
    refusal = await overnight_refusal(delivery, tainted)

    assert delivery.notify_runs() == []
    assert delivery.sent == []
    held = {row["target_id"]: row for row in notifications(delivery)}
    assert held[result]["decision"] == "overnight"
    assert held[refusal]["decision"] == "overnight"
    assert await delivery.review_count() == 2  # the in-app badge never waits

    await delivery.move(green, "in_progress", "backlog")  # a natural break flushes Quiet only
    await release(delivery, RELEASE_AT - timedelta(minutes=5))
    assert delivery.notify_runs() == []
    assert delivery.sent == []

    await release(delivery, RELEASE_AT)

    [run] = delivery.notify_runs()
    assert run["profile_id"] == delivery.master_id
    assert run["packet"]["body"]["batch"]["count"] == 2
    [(_, push, _)] = delivery.sent
    assert "1 result from overnight" in push.title
    released = {row["target_id"]: row["released_at"] for row in notifications(delivery)}
    assert released[result] is not None
    assert released[refusal] is not None

    await release(delivery, RELEASE_AT + timedelta(minutes=5))
    assert len(delivery.notify_runs()) == 1
    assert len(delivery.sent) == 1

    queue = await delivery.http.get("/v1/review", params={"kind": "result"})
    assert queue.status_code == 200, queue.text
    [item] = queue.json()["items"]
    assert item["id"] == str(result)
    assert item["payload"]["batch"] == "overnight"
