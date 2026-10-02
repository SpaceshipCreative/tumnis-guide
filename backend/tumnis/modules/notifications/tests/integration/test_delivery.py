"""Discord delivery through the master (P2-16, FR-8.1, FR-8.2, FR-8.4, REL-3): every
notification that goes now becomes one `notify` run of the master profile's `focus`
skill (which posts to the one Discord channel itself); Quiet with a task In progress holds
everything until the next natural break; each attempt is a `delivery_attempts` row, and a
delivery that keeps failing is retried, then dead-lettered. The in-app badge never waits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tumnis.modules.notifications.tests.integration._delivery import DeliveryWorld

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

ATTEMPTS = 3


@pytest.mark.req("FR-8.4")
@pytest.mark.wp("P2-16")
@pytest.mark.xfail(strict=True, reason="spec:P2-16")
async def test_quiet_in_progress_nothing_dispatched_then_flush(delivery: DeliveryWorld) -> None:
    """T-P2-16-04
    At Quiet with a task In progress a new review item dispatches no notify run. When the
    task leaves In progress (the next natural break) exactly one notify run goes to the
    master, for the batch of one item; a later break with nothing held sends nothing more.
    """
    delivery.master_says("1 item waited while you worked.")
    task = await delivery.task("Write proposal")
    await delivery.level("quiet")
    await delivery.move(task, "in_progress")

    await delivery.review_item(task)
    assert delivery.notify_runs() == []

    await delivery.move(task, "backlog")  # the natural break
    [run] = delivery.notify_runs()
    assert run["profile_id"] == delivery.master_id
    assert run["packet"]["skill"] == "focus"
    assert run["packet"]["body"]["batch"]["count"] == 1
    [attempt] = delivery.discord_attempts()
    assert attempt["status"] == "sent"
    assert attempt["run_id"] == run["id"]

    await delivery.move(task, "in_progress", "backlog")
    assert len(delivery.notify_runs()) == 1


@pytest.mark.req("FR-8.2")
@pytest.mark.wp("P2-16")
@pytest.mark.xfail(strict=True, reason="spec:P2-16")
async def test_only_master_receives_notify_runs(delivery: DeliveryWorld) -> None:
    """T-P2-16-05
    With a project agent on the task's project, a focus event and a review item at Nudge
    each become one notify run, and every notify run targets the master profile with its
    `focus` skill; the project agent gets none. The focus event's packet names the task,
    the level and the event's rule as fired.
    """
    delivery.master_says()
    task = await delivery.task("Write proposal")
    agent = await delivery.project_agent()
    await delivery.level("nudge")

    event_id = await delivery.focus_event("block_start", task, "nudge")
    await delivery.review_item(task)

    runs = delivery.notify_runs()
    assert len(runs) == 2
    assert {run["profile_id"] for run in runs} == {delivery.master_id}
    assert {run["packet"]["skill"] for run in runs} == {"focus"}
    assert [r for r in delivery.runs_of(agent) if r["kind"] == "notify"] == []
    [focus] = [r for r in runs if r["packet"]["body"].get("event")]
    event = focus["packet"]["body"]["event"]
    assert event["id"] == str(event_id)
    assert event["kind"] == "block_start"
    assert event["level"] == "nudge"
    assert event["rule"] == "nudge:block_start"
    assert focus["packet"]["body"]["task"]["title"] == "Write proposal"
    assert sorted(a["status"] for a in delivery.discord_attempts()) == ["sent", "sent"]


@pytest.mark.req("FR-8.1", "REL-3")
@pytest.mark.wp("P2-16")
@pytest.mark.xfail(strict=True, reason="spec:P2-16")
async def test_failed_delivery_retries_then_dead_letters(delivery: DeliveryWorld) -> None:
    """T-P2-16-10
    The master's focus skill fails every time (the Discord gateway is down): the delivery
    is attempted exactly the limit's number of times, each attempt recorded `failed` with
    its own notify run, and then it is dead-lettered: one open dead letter in Settings
    names the delivery subscriber. The in-app badge still counts the item.
    """
    delivery.master_fails()
    delivery.retry_quickly(ATTEMPTS)
    task = await delivery.task("Write proposal")
    await delivery.level("nudge")

    await delivery.review_item(task)

    attempts = delivery.discord_attempts()
    assert [a["status"] for a in attempts] == ["failed"] * ATTEMPTS
    assert len({a["run_id"] for a in attempts}) == ATTEMPTS
    assert len(delivery.notify_runs()) == ATTEMPTS
    letters = [
        d
        for d in await delivery.dead_letters()
        if d["subscriber"] == "notifications.deliver_notification"
    ]
    assert len(letters) == 1
    assert letters[0]["status"] == "open"
    assert await delivery.review_count() == 1


@pytest.mark.req("FR-8.1")
@pytest.mark.wp("P2-16")
@pytest.mark.xfail(strict=True, reason="spec:P2-16")
async def test_review_badge_counts_every_item(delivery: DeliveryWorld) -> None:
    """T-P2-16-11
    At Quiet with a task In progress two review items are held from Discord and push, yet
    the in-app review badge counts both at once; after the break the badge still counts
    them, and Discord gets one notify run for the batch of two.
    """
    delivery.master_says()
    assert (await delivery.subscribe()).status_code == 201
    task = await delivery.task("Write proposal")
    await delivery.level("quiet")
    await delivery.move(task, "in_progress")

    await delivery.review_item(task)
    await delivery.review_item(task)
    assert await delivery.review_count() == 2
    assert delivery.notify_runs() == []
    assert delivery.sent == []

    await delivery.move(task, "backlog")
    assert await delivery.review_count() == 2
    [run] = delivery.notify_runs()
    assert run["packet"]["body"]["batch"]["count"] == 2
