"""Browser push as a channel of `notifications` (P4-05, FR-8.3, FR-8.4): the same events
that reach Discord, at the same moments, under P2-16's level and batching rules, through
the fake push service.

P2-16's delivery table (plan, P2-16 "Interfaces"): focus events and review items reach the
human; at Quiet with a task In progress everything is batched until the next natural break
(the task leaves In progress, or the day ends); at every other level, and at Quiet with no
task In progress, they go at once. Discord itself arrives with P2-16, on the same
`delivery_decision`; until then "push is attempted exactly when Discord is" means push
follows that table, written out below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tumnis.modules.notifications.tests.integration._push import PushWorld

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

LEVELS = ("quiet", "nudge", "coach", "guardrail")
FOCUS_KINDS = (
    "block_start",
    "not_started",
    "check_in_due",
    "switched",
    "stuck",
    "block_end",
    "day_end",
)


def _review_pushes(world: PushWorld) -> list[Any]:
    return [p for _sub, p, _ttl in world.sent if p.kind == "estimate_outlier"]


@pytest.mark.req("FR-8.4", "FR-8.3")
@pytest.mark.wp("P4-05")
@pytest.mark.xfail(strict=True, reason="spec:P4-05")
async def test_push_follows_focus_level(push: PushWorld) -> None:
    """T-P4-05-04
    At Quiet with a task In progress a new review item is batched and nothing is sent; when
    the task leaves In progress (the break) exactly one push goes, for the batch, and a
    later break sends nothing more. At Quiet with nothing In progress, and at Nudge, Coach
    and Guardrail even with a task In progress, each item is pushed at once.
    """
    assert (await push.subscribe()).status_code == 201
    task = await push.task("Write proposal")
    await push.level("quiet")
    await push.move(task, "in_progress")

    batched = await push.review_item(task)
    assert push.sent == []
    assert push.pushed(batched) == []

    await push.move(task, "backlog")  # the natural break
    assert len(push.sent) == 1
    (batch,) = [p for _sub, p, _ttl in push.sent]
    assert batch.url == "/review"
    assert push.pushed(batched) == []  # the batch push names no single item

    await push.move(task, "in_progress", "backlog")
    assert len(push.sent) == 1  # nothing waits, so the next break sends nothing

    idle = await push.review_item(task)  # Quiet, nothing In progress
    assert [p.url for p in push.pushed(idle)] == [f"/review?kind=estimate_outlier&item={idle}"]

    for level in ("nudge", "coach", "guardrail"):
        other = await push.task(f"Busy at {level}")
        await push.level(level)
        await push.move(other, "in_progress")
        item = await push.review_item(other)
        assert len(push.pushed(item)) == 1, level
        await push.move(other, "backlog")
    assert len(_review_pushes(push)) == 4  # idle + the three levels; the batch is apart


@pytest.mark.req("FR-8.3")
@pytest.mark.wp("P4-05")
@pytest.mark.xfail(strict=True, reason="spec:P4-05")
async def test_same_events_as_discord(push: PushWorld) -> None:
    """T-P4-05-05
    For each event kind in P2-16's delivery table (every focus event kind and a review
    item), at every level, with and without a task In progress: a push for that item is
    attempted at once exactly when the table says "now" (not Quiet-and-In-progress), and is
    held otherwise. Events outside the table (a ping, a task's status changing with
    nothing held) push nothing.
    """
    assert (await push.subscribe()).status_code == 201
    task = await push.task("Write proposal")
    wrong: list[tuple[str, str, bool, int]] = []
    for level in LEVELS:
        await push.level(level)
        for in_progress in (False, True):
            if in_progress:
                await push.move(task, "in_progress")
            now = not (level == "quiet" and in_progress)
            tags = [(kind, await push.focus_event(kind, task, level)) for kind in FOCUS_KINDS]
            tags.append(("review_item", await push.review_item(task)))
            for kind, tag in tags:
                got = len(push.pushed(tag))
                if got != (1 if now else 0):
                    wrong.append((kind, level, in_progress, got))
            if in_progress:
                await push.move(task, "backlog")
    assert wrong == []

    await push.level("quiet")  # no focus session starts on the moves below
    before = len(push.sent)
    await push.emit("test.ping", note="not a notification")
    await push.move(task, "in_progress", "backlog")
    assert len(push.sent) == before


@pytest.mark.req("FR-8.3")
@pytest.mark.wp("P4-05")
@pytest.mark.xfail(strict=True, reason="spec:P4-05")
async def test_gone_subscription_deleted(push: PushWorld) -> None:
    """T-P4-05-06
    The push service answers 410 for one subscription: it is deleted, with one `gone`
    attempt. It answers 500 for another: the delivery is retried (three attempts, each
    recorded `failed`) and the subscription's failures are counted. The next push to it
    succeeds: `sent`, failures back to 0 and `last_success_at` set.
    """
    gone = "https://fcm.googleapis.com/fcm/send/gone-subscription"
    flaky = "https://updates.push.services.mozilla.com/wpush/v2/flaky-subscription"
    assert (await push.subscribe(gone)).status_code == 201
    assert (await push.subscribe(flaky)).status_code == 201
    push.fake.script(gone, 410)
    push.fake.script(flaky, 500, 500, 500)
    await push.level("nudge")
    task = await push.task("Write proposal")

    await push.review_item(task)

    subs = {s["endpoint"]: s for s in push.subscriptions()}
    assert set(subs) == {flaky}
    assert subs[flaky]["failures"] == 3
    by_endpoint: dict[str, list[tuple[str, int | None]]] = {}
    ids = {s["id"]: s["endpoint"] for s in push.subscriptions()}
    for row in push.attempts():
        endpoint = ids.get(row["subscription_id"], gone)
        by_endpoint.setdefault(endpoint, []).append((row["status"], row["status_code"]))
        assert row["channel"] == "push"
    assert by_endpoint == {
        gone: [("gone", 410)],
        flaky: [("failed", 500)] * 3,
    }

    await push.review_item(task)
    after = {s["endpoint"]: s for s in push.subscriptions()}[flaky]
    assert after["failures"] == 0
    assert after["last_success_at"] is not None
    assert push.attempts()[-1]["status"] == "sent"
