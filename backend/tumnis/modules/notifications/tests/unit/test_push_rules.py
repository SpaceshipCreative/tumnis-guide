"""The browser push rules (P4-05, FR-8.3, SEC-5, Data flow rule 6): the deep link of each
review kind, what a push payload may carry, and which push endpoints Tumnis may call.
Pure functions; no clock, no network."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tumnis.modules.notifications import rules

ITEM = UUID("0199aa00-0000-7000-8000-0000000004a1")
# Registered review kinds on main (R-05) and the plan's examples.
KINDS = (
    "approval",
    "question",
    "result",
    "proposal",
    "drift",
    "plan_issue",
    "low_confidence_label",
    "decision_unavailable",
    "provisioning_failed",
)
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
KIND_SLUG = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


@pytest.mark.req("FR-8.3")
@pytest.mark.wp("P4-05")
@pytest.mark.parametrize("kind", KINDS)
def test_deep_link_per_review_kind(kind: str) -> None:
    """T-P4-05-01
    Each review kind maps to `/review?kind=<kind>&item=<id>`: a same-origin path whose
    params are exactly the review route's (`kind` a registry slug, `item` a uuid), and the
    push payload for the item carries that link with the item id as its tag.
    """
    item = rules.ReviewItemLite(id=ITEM, kind=kind, project_name="Acme site")
    link = rules.deep_link_for(item)
    assert link == f"/review?kind={kind}&item={ITEM}"
    parts = urlsplit(link)
    assert (parts.scheme, parts.netloc, parts.path) == ("", "", "/review")
    params = parse_qs(parts.query, strict_parsing=True)
    assert params == {"kind": [kind], "item": [str(ITEM)]}
    assert KIND_SLUG.match(params["kind"][0])
    payload = rules.push_payload(item)
    assert (payload.url, payload.tag, payload.kind) == (link, str(ITEM), kind)


_sentinel = st.from_regex(r"\A[A-Z][a-z]{5,10}[0-9]{4}\Z")
_words = st.text(alphabet=st.characters(codec="utf-8", exclude_categories=["Cs"]), max_size=200)


@st.composite
def _items(draw: st.DrawFn) -> tuple[rules.ReviewItemLite, str]:
    secret = draw(_sentinel)
    email = draw(st.emails())
    body = " ".join(draw(st.permutations([draw(_words), secret, email])))
    project = draw(st.none() | st.text(max_size=120) | st.just(f"Client {email}"))
    kind = draw(st.sampled_from(KINDS))
    return rules.ReviewItemLite(id=ITEM, kind=kind, project_name=project, body=body), secret


@pytest.mark.req("Data flow rule 6", "FR-8.3")
@pytest.mark.wp("P4-05")
@settings(max_examples=200, deadline=None)
@given(case=_items(), focus_message=st.text(max_size=200), focus_secret=_sentinel)
def test_payload_has_no_item_content(
    case: tuple[rules.ReviewItemLite, str], focus_message: str, focus_secret: str
) -> None:
    """T-P4-05-02
    Over review items whose bodies hold free text, a secret word and an email address (and
    project names that may hold an email): the push payload's JSON never contains the body
    text or any email address, and stays under the 4 KB push bound once encrypted. The
    same holds for a focus event, whose message (it names the task) never leaves.
    """
    item, secret = case
    for payload in (
        rules.push_payload(item),
        rules.push_payload(
            rules.FocusEventLite(
                id=ITEM, kind="check_in_due", message=f"{focus_message} {focus_secret}"
            )
        ),
    ):
        text = payload.model_dump_json()
        assert secret not in text
        assert focus_secret not in text
        assert item.body is not None
        assert item.body not in text
        assert not EMAIL.search(text), text
        assert not EMAIL.search(json.loads(text)["title"])
        assert rules.encrypted_size(len(text.encode())) < rules.MAX_PUSH_BYTES


ALLOWED = (
    "https://fcm.googleapis.com/fcm/send/fake-subscription-id",
    "https://updates.push.services.mozilla.com/wpush/v2/fake-subscription-id",
    "https://web.push.apple.com/fake-subscription-id",
    "https://api.push.apple.com/3/device/fake",
    "https://wns2-by3p.notify.windows.com/w/?token=fake",
    "https://FCM.googleapis.com/fcm/send/fake",
)
REFUSED = (
    "http://fcm.googleapis.com/fcm/send/fake",  # not https
    "https://push.example.com/fake",  # unknown host
    "https://fcm.googleapis.com.example.org/fake",  # allow-listed name as a prefix
    "https://evilfcm.googleapis.com/fake",  # not the host, not a listed suffix
    "https://push.apple.com.example.org/fake",
    "https://notify.windows.com/fake",  # the bare suffix is not a push host
    "https://192.0.2.10/fake",  # IPv4 literal
    "https://[2001:db8::10]/fake",  # IPv6 literal
    "https://2130706433/fake",  # an integer IPv4
    "https://user:secret@fcm.googleapis.com/fake",  # userinfo
    "https://fcm.googleapis.com:8443/fake",  # not the https port
    "wss://fcm.googleapis.com/fake",
    "ftp://fcm.googleapis.com/fake",
    "fcm.googleapis.com/fake",
    "",
    "not a url",
)


@pytest.mark.req("SEC-5", "FR-8.3")
@pytest.mark.wp("P4-05")
def test_endpoint_allowlist() -> None:
    """T-P4-05-03
    Only https endpoints on the push services' hosts are allowed; http, unknown hosts,
    look-alike names, IP literals, userinfo and other ports are refused.
    """
    assert [e for e in ALLOWED if not rules.endpoint_allowed(e)] == []
    assert [e for e in REFUSED if rules.endpoint_allowed(e)] == []


# --- P2-16's delivery rules as P4-05 uses them (no spec IDs: the seam's own tests) -------


@pytest.mark.req("FR-8.4")
@pytest.mark.wp("P4-05")
@pytest.mark.parametrize("level", ["quiet", "nudge", "coach", "guardrail"])
@pytest.mark.parametrize("in_progress", [False, True])
@pytest.mark.parametrize("kind", ["approval", "focus.check_in_due"])
def test_delivery_decision_batches_only_quiet_while_in_progress(
    level: rules.Level, in_progress: bool, kind: str
) -> None:
    expected = "batch" if level == "quiet" and in_progress else "now"
    assert rules.delivery_decision(level, in_progress, kind) == expected


@pytest.mark.req("FR-8.4")
@pytest.mark.wp("P4-05")
def test_flush_due_at_the_next_break() -> None:
    now = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
    held = [rules.NotificationView(id=ITEM, kind="approval", created_at=now)]
    day_end = now + timedelta(hours=3)
    assert rules.flush_due(held, any_task_in_progress=False, now=now, day_end_at=day_end)
    assert not rules.flush_due(held, any_task_in_progress=True, now=now, day_end_at=day_end)
    assert rules.flush_due(held, any_task_in_progress=True, now=day_end, day_end_at=day_end)
    assert not rules.flush_due([], any_task_in_progress=False, now=now, day_end_at=day_end)


@pytest.mark.req("FR-8.3")
@pytest.mark.wp("P4-05")
def test_batch_payload_counts_and_links_to_the_queue() -> None:
    one, three = rules.batch_payload(1), rules.batch_payload(3)
    assert (one.title, three.title) == ("Waiting on you: 1 item", "Waiting on you: 3 items")
    assert (three.url, three.tag, three.kind) == ("/review", "batch", "batch")


@pytest.mark.req("FR-8.3")
@pytest.mark.wp("P4-05")
def test_focus_push_opens_the_dashboard_and_odd_kinds_link_by_item() -> None:
    focus = rules.push_payload(rules.FocusEventLite(id=ITEM, kind="block_start"))
    assert (focus.url, focus.kind, focus.title) == (
        "/",
        "focus.block_start",
        "Focus: time to start",
    )
    odd = rules.ReviewItemLite(id=ITEM, kind="Not a slug")
    assert rules.deep_link_for(odd) == f"/review?item={ITEM}"
    bare = rules.ReviewItemLite(id=ITEM, kind="approval", project_name="ops@example.com")
    assert rules.push_payload(bare).title == "Waiting on you: approval"
