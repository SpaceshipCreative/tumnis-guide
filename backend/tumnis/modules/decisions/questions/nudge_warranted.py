"""The nudge-warranted Noul (P2-15, FR-11.4): is a focus nudge (a gateable focus event:
`not_started` or `check_in_due`) warranted now, or would it interrupt more than it helps?

Fields sent: the level, the event kind, minutes into the block or session, the task's
status, the latest responses and the minutes since the last nudge. Never the task's
title, first action or any other text (R-37: activity counts and kinds only).

The Noul may only suppress (FR-11.4). The answer is read as `NudgeWarranted(p, confidence,
fallback, threshold)`: `p` the probability that a nudge is warranted now, `confidence` its
distance from 0.5 scaled to 0..1, and `threshold` the confidence a "no" needs to suppress:
`1 - 2 * t_no` of the threshold in force (stricter for a fallback answer), as
`approval_need` reads its Noul.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

LEVEL_MAX: Final = 10  # catalogue: level
KIND_MAX: Final = 20  # catalogue: event_kind, task_status and each recent response
RESPONSES_MAX: Final = 5  # catalogue: recent_responses
MAIN_QUESTION: Final = "nudge"


@dataclass(frozen=True)
class NudgeWarranted:
    p: float  # probability that a nudge is warranted now
    confidence: float
    fallback: bool
    threshold: float  # the confidence a "no" needs to suppress the nudge


def inputs(  # the catalogue's fields, spelled out
    *,
    level: str,
    event_kind: str,
    minutes_into_block: int | None,
    task_status: str | None,
    recent_responses: Iterable[str],
    minutes_since_last_nudge: int | None,
) -> dict[str, Any]:
    """The fields the Noul is asked with, cut to the catalogue's limits."""
    found: dict[str, Any] = {
        "level": level[:LEVEL_MAX],
        "event_kind": event_kind[:KIND_MAX],
        "recent_responses": [r[:KIND_MAX] for r in recent_responses][:RESPONSES_MAX],
    }
    if minutes_into_block is not None:
        found["minutes_into_block"] = max(minutes_into_block, 0)
    if task_status is not None:
        found["task_status"] = task_status[:KIND_MAX]
    if minutes_since_last_nudge is not None:
        found["minutes_since_last_nudge"] = max(minutes_since_last_nudge, 0)
    return found


def read_answer(
    answers: Mapping[str, Any], *, fallback: bool, t_no: float | None
) -> NudgeWarranted | None:
    """The answer as `NudgeWarranted`; None when there is no Noul answer to read."""
    answer = answers.get(MAIN_QUESTION)
    p = getattr(answer, "noul", None)
    if not isinstance(p, (int, float)):
        return None
    threshold = 1.0 if t_no is None else min(max(1 - 2 * t_no, 0.0), 1.0)
    return NudgeWarranted(
        p=float(p), confidence=abs(float(p) - 0.5) * 2, fallback=fallback, threshold=threshold
    )
