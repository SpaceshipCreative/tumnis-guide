"""The task state machine allows exactly the plan's matrix (P0-18, FR-3.2): every
(from, to, actor, label) cell is either an edge or 409 `transition_not_allowed`, and agents
only plan, start, ask and post results."""

from __future__ import annotations

import itertools

import pytest

from tumnis.modules.tasks.tests.unit.transition_matrix import (
    ACTORS,
    EXPECTED,
    LABELS,
    STATUSES,
)

CELLS = list(itertools.product(STATUSES, STATUSES, ACTORS, LABELS))


@pytest.mark.req("FR-3.2")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
@pytest.mark.parametrize(("frm", "to", "actor", "label"), CELLS)
def test_every_pair_is_allowed_or_rejected(
    frm: str, to: str, actor: str, label: str | None
) -> None:
    """T-P0-18-01
    6 x 6 x 3 x 4 cells (label or pending) match the matrix: an allowed cell returns its
    edge, any other raises TransitionNotAllowed with code `transition_not_allowed`.
    """
    from tumnis.modules.tasks.rules import (  # noqa: PLC0415
        ActorKind,
        Edge,
        Label,
        Status,
        TransitionNotAllowed,
        check_transition,
    )

    args = (Status(frm), Status(to), ActorKind(actor), None if label is None else Label(label))
    if EXPECTED[frm, to, actor, label]:
        assert isinstance(check_transition(*args), Edge)
    else:
        with pytest.raises(TransitionNotAllowed) as refused:
            check_transition(*args)
        assert refused.value.code == "transition_not_allowed"


@pytest.mark.req("FR-3.2")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
def test_agent_edges_are_a_subset_of_the_diagram() -> None:
    """T-P0-18-02
    The edges an agent may take are backlog -> today (plan), backlog or today ->
    in_progress (start), in_progress -> waiting_on_human (ask) and in_progress -> in_review
    (result): never done, never backlog.
    """
    from tumnis.modules.tasks.rules import TRANSITIONS, ActorKind, Trigger  # noqa: PLC0415

    agent = {pair: edge for pair, edge in TRANSITIONS.items() if ActorKind.AGENT in edge.actors}
    assert set(agent) == {
        ("backlog", "today"),
        ("backlog", "in_progress"),
        ("today", "in_progress"),
        ("in_progress", "waiting_on_human"),
        ("in_progress", "in_review"),
    }
    assert {edge.trigger for edge in agent.values()} == {
        Trigger.PLAN,
        Trigger.START,
        Trigger.ASK,
        Trigger.RESULT,
    }
    assert all(to not in {"done", "backlog"} for _, to in agent)
