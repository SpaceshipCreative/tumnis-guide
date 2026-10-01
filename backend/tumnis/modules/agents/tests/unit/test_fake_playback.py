"""The fake runner's phase 1 playback in the compose.test stack (SEED, Scott decision 37;
R-37): a stored `<profile>/<skill>` script names a recording under
backend/tests/fakes/recordings/runner, whose output is fitted to the packet before it is
answered: a plan reply's `title:<task title>` picks become the ids of the packet's
candidates with those titles, and an enrichment reply's sentinel `task_id` becomes the
packet's own task (as tests/fakes/fake_runner.py does for the backend suites)."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.req("A1.1", "A1.2", "A2.6"), pytest.mark.wp("SEED")]

SENTINEL = "00000000-0000-0000-0000-000000000000"
INVOICE = "0199aa00-0000-7000-8000-000000000001"
LOGOS = "0199aa00-0000-7000-8000-000000000002"
GUEST = "0199aa00-0000-7000-8000-000000000003"
OWN_TASK = "0199aa00-0000-7000-8000-000000000004"


def _plan_packet() -> dict[str, Any]:
    return {
        "body": {
            "candidates": [
                {"task_id": INVOICE, "title": "Invoice Acme for phase one"},
                {"task_id": LOGOS, "title": "Send logo drafts to Acme"},
                {"task_id": GUEST, "title": "Book a guest for lesson three"},
            ]
        }
    }


def test_plan_reply_titles_become_candidate_ids() -> None:
    """T-SEED-14
    A recorded plan reply's `title:` picks and alternates become the ids of the packet's
    candidates with those titles; a title no candidate has becomes the fixed unknown id
    (so `check_picks` refuses the reply, as `plan__unknown_task` expects); a plain id is
    left as it is; the reason and the rest of the reply are untouched."""
    from tumnis.modules.agents.adapters.fake import (  # noqa: PLC0415
        UNKNOWN_TASK_ID,
        scripted_output,
    )

    reply: dict[str, Any] = {
        "schema_version": 1,
        "picks": [
            {"task_id": "title:Invoice Acme for phase one", "reason": "Pays on receipt"},
            {"task_id": "title:No such task", "reason": "Made up"},
            {"task_id": LOGOS, "reason": "Already an id"},
        ],
        "alternates": ["title:Book a guest for lesson three"],
        "notes": None,
    }
    out = scripted_output(reply, _plan_packet())
    assert out == {
        "schema_version": 1,
        "picks": [
            {"task_id": INVOICE, "reason": "Pays on receipt"},
            {"task_id": UNKNOWN_TASK_ID, "reason": "Made up"},
            {"task_id": LOGOS, "reason": "Already an id"},
        ],
        "alternates": [GUEST],
        "notes": None,
    }
    assert reply["picks"][0]["task_id"] == "title:Invoice Acme for phase one"  # not mutated


def test_enrich_reply_sentinel_becomes_the_packet_task() -> None:
    """T-SEED-15
    A recorded enrichment reply whose `task_id` is the sentinel answers for the packet's
    own task (`body.task.id`); any other `task_id` stays; no output stays None."""
    from tumnis.modules.agents.adapters.fake import (  # noqa: PLC0415
        TASK_ID_SENTINEL,
        scripted_output,
    )

    assert TASK_ID_SENTINEL == SENTINEL
    packet = {"body": {"task": {"id": OWN_TASK, "title": "Send Acme the March invoice"}}}
    reply = {"schema_version": 1, "task_id": SENTINEL, "first_action": "Open the invoice"}
    assert scripted_output(reply, packet) == {**reply, "task_id": OWN_TASK}
    other = scripted_output({**reply, "task_id": INVOICE}, packet)
    assert other is not None
    assert other["task_id"] == INVOICE
    assert scripted_output(None, packet) is None


def test_recordings_load_by_bare_name_only() -> None:
    """T-SEED-16
    A script's `result` names a file in the runner recordings folder: a bare name loads
    its JSON; a path, a parent reference or a missing file is refused (ValueError), so a
    posted script never reads outside the folder."""
    from tumnis.modules.agents.adapters.fake import (  # noqa: PLC0415
        load_recording,
    )

    plan = load_recording("plan__monday_four_picks.result.json")
    assert plan["picks"][0]["task_id"] == "title:Invoice Acme for phase one"
    enrich = load_recording("enrich__hybrid_invoice.result.json")
    assert enrich["estimate_minutes"] == 20
    for bad in (
        "../recordings/runner/plan__no_reason.result.json",
        "runner/plan__no_reason.result.json",
        "/etc/hostname",
        "..",
        "",
        "no_such_recording.result.json",
    ):
        with pytest.raises(ValueError):  # noqa: PT011  # the one error a bad name raises
            load_recording(bad)
