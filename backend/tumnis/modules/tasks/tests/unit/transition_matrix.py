"""The task state machine as a literal copy of the plan's matrix (P0-18, FR-3.2). No
assertions live here: T-P0-18-01, 03 and 04 read `EXPECTED`.

It is written out by hand, not derived from `tasks.rules.TRANSITIONS`, so a change to the
table without a change here (a spec change Scott approves) fails the tests.

Rows are the status a task is in, columns the status asked for; a cell names the actors
allowed (H human, A agent, S system). A missing cell is 409 `transition_not_allowed` for
every actor, including from = to. `in_progress -> done` is for Human-labelled tasks only
(not AI, not Hybrid, not a pending label).
"""

from __future__ import annotations

import itertools
from typing import Final

STATUSES: Final = ("backlog", "today", "in_progress", "waiting_on_human", "in_review", "done")
ACTORS: Final = ("human", "agent", "system")
LABELS: Final = ("human", "ai", "hybrid", None)  # None: label pending (R-08)

_LETTERS: Final = {"H": "human", "A": "agent", "S": "system"}

# (from, to): actor letters
MATRIX: Final[dict[tuple[str, str], str]] = {
    ("backlog", "today"): "HAS",
    ("backlog", "in_progress"): "HA",
    ("today", "backlog"): "HS",
    ("today", "in_progress"): "HA",
    ("in_progress", "backlog"): "H",
    ("in_progress", "waiting_on_human"): "A",
    ("in_progress", "in_review"): "A",
    ("in_progress", "done"): "H",
    ("waiting_on_human", "backlog"): "H",
    ("waiting_on_human", "in_progress"): "HS",
    ("in_review", "backlog"): "H",
    ("in_review", "in_progress"): "H",
    ("in_review", "done"): "H",
    ("done", "backlog"): "H",
}
HUMAN_LABEL_ONLY: Final = frozenset({("in_progress", "done")})


def _allowed(frm: str, to: str, actor: str, label: str | None) -> bool:
    letters = MATRIX.get((frm, to), "")
    if actor not in {_LETTERS[letter] for letter in letters}:
        return False
    return (frm, to) not in HUMAN_LABEL_ONLY or label == "human"


# (from, to, actor, label) -> allowed: 6 x 6 x 3 x 4 cells.
EXPECTED: Final[dict[tuple[str, str, str, str | None], bool]] = {
    (frm, to, actor, label): _allowed(frm, to, actor, label)
    for frm, to, actor, label in itertools.product(STATUSES, STATUSES, ACTORS, LABELS)
}
