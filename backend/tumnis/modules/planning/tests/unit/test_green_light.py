"""The green light (P4-04, FR-4.5, SAF-1): an unattended window runs only AI tasks the user
queued for it, with acceptance criteria, untainted (P2-08's `may_run_unattended`), in a
project that is not paused, with the kill switch off. `green_light` names the first reason
a task may not run, in a fixed order, or None when it may."""

import importlib
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

LABELS = (None, "human", "ai", "hybrid")
STATUSES = ("backlog", "today", "in_progress", "waiting_on_human", "in_review", "done")


def _rules() -> Any:
    return importlib.import_module("tumnis.modules.planning.rules")


def _task(**fields: Any) -> Any:
    base: dict[str, Any] = {
        "label": "ai",
        "status": "today",
        "queued": True,
        "has_acceptance_criteria": True,
    }
    return _rules().TaskLite(**{**base, **fields})


def _flags(**flags: bool) -> dict[str, bool]:
    return {"may_run_unattended": True, "project_paused": False, "kill_switch": False, **flags}


# (task fields, flags, expected refusal value or None)
CASES: dict[str, tuple[dict[str, Any], dict[str, bool], str | None]] = {
    "green": ({}, {}, None),
    "green_from_backlog": ({"status": "backlog"}, {}, None),
    "kill_switch": ({}, {"kill_switch": True}, "kill_switch"),
    "paused": ({}, {"project_paused": True}, "paused"),
    "not_queued": ({"queued": False}, {}, "not_queued"),
    "done": ({"status": "done"}, {}, "done"),
    "human": ({"label": "human"}, {}, "not_ai"),
    "hybrid": ({"label": "hybrid"}, {}, "not_ai"),
    "pending_label": ({"label": None}, {}, "not_ai"),
    "tainted": ({}, {"may_run_unattended": False}, "tainted"),
    "waiting_on_human": ({"status": "waiting_on_human"}, {}, "waiting_on_human"),
    "no_acceptance_criteria": ({"has_acceptance_criteria": False}, {}, "no_acceptance_criteria"),
    # The order: kill switch, paused, not queued, done, not AI, tainted, waiting on a human,
    # no acceptance criteria. Each case below holds every later reason too.
    "kill_switch_first": (
        {"queued": False, "status": "done", "label": "human"},
        {"kill_switch": True, "project_paused": True, "may_run_unattended": False},
        "kill_switch",
    ),
    "paused_before_queue": (
        {"queued": False, "status": "done"},
        {"project_paused": True, "may_run_unattended": False},
        "paused",
    ),
    "not_queued_before_done": (
        {"queued": False, "status": "done", "label": "human"},
        {"may_run_unattended": False},
        "not_queued",
    ),
    "done_before_label": (
        {"status": "done", "label": "human", "has_acceptance_criteria": False},
        {"may_run_unattended": False},
        "done",
    ),
    "not_ai_before_taint": (
        {"label": "hybrid", "status": "waiting_on_human"},
        {"may_run_unattended": False},
        "not_ai",
    ),
    "taint_before_waiting": (
        {"status": "waiting_on_human", "has_acceptance_criteria": False},
        {"may_run_unattended": False},
        "tainted",
    ),
    "waiting_before_criteria": (
        {"status": "waiting_on_human", "has_acceptance_criteria": False},
        {},
        "waiting_on_human",
    ),
}


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
@pytest.mark.xfail(strict=True, reason="spec:P4-04")
@pytest.mark.parametrize("case", list(CASES))
def test_green_light_table(case: str) -> None:
    """T-P4-04-03
    Every refusal reason and the pass case, and the order the reasons are checked in: kill
    switch, paused, not queued, done, not AI, tainted, waiting on a human, no acceptance
    criteria.
    """
    rules = _rules()
    fields, flags, expected = CASES[case]

    refusal = rules.green_light(_task(**fields), **_flags(**flags))

    if expected is None:
        assert refusal is None
    else:
        assert refusal == rules.Refusal(expected)
        assert refusal.value == expected


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
@pytest.mark.xfail(strict=True, reason="spec:P4-04")
def test_every_refusal_has_plain_words() -> None:
    """T-P4-04-03
    Each refusal reason has the plain words the day close and its review item show; the two
    the day close names in J7 read as the plan writes them.
    """
    rules = _rules()
    words = rules.REFUSAL_WORDS
    assert set(words) == set(rules.Refusal)
    assert all(isinstance(text, str) and text.strip() for text in words.values())
    assert words[rules.Refusal.tainted] == "From outside content: needs you"
    assert words[rules.Refusal.paused] == "Project paused"


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P4-04")
@pytest.mark.xfail(strict=True, reason="spec:P4-04")
@given(
    label=st.sampled_from(LABELS),
    status=st.sampled_from(STATUSES),
    queued=st.booleans(),
    has_criteria=st.booleans(),
    project_paused=st.booleans(),
    kill_switch=st.booleans(),
)
def test_tainted_never_green(  # noqa: PLR0917  # one argument per drawn input
    label: str | None,
    status: str,
    queued: bool,
    has_criteria: bool,
    project_paused: bool,
    kill_switch: bool,
) -> None:
    """T-P4-04-04
    Whatever else holds, `may_run_unattended=False` (a tainted task, P2-08) is never a
    green light (SAF-1).
    """
    rules = _rules()
    task = rules.TaskLite(
        label=label, status=status, queued=queued, has_acceptance_criteria=has_criteria
    )

    refusal = rules.green_light(
        task, may_run_unattended=False, project_paused=project_paused, kill_switch=kill_switch
    )

    assert refusal is not None
