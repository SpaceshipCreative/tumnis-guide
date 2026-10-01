"""The approval-need Noul (P2-05, FR-5.6, FR-11.4): is an action the project's policy does
not name gated, or equivalent in effect to a gated one?

Fields sent: the action class, a short summary of the description, the target, and the
policy's gated and allowed classes. Never a message body or any other outside text: the
description is the agent's own words, cut to the catalogue's limit.

`approval_need` has no yes band (P1-02): only a confident "not gated" is applied. The
answer is read as `ApprovalNeed(p, confidence, fallback, threshold)`, where `confidence`
is the Noul's distance from 0.5 scaled to 0..1 and `threshold` the confidence a "not
gated" answer needs: `1 - 2 * t_no` of the threshold in force (stricter for a fallback
answer), so `agents.rules.approval_need` applies exactly what the decisions route does.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

SUMMARY_MAX: Final = 500  # catalogue: action_summary
TARGET_MAX: Final = 200  # catalogue: target
CLASS_MAX: Final = 60  # catalogue: action_class and the policy lists' items
POLICY_MAX: Final = 20  # catalogue: items per policy list
MAIN_QUESTION: Final = "gated"


@dataclass(frozen=True)
class ApprovalNeed:
    p: float  # probability that the action is gated
    confidence: float
    fallback: bool
    threshold: float  # the confidence a "not gated" answer needs to be applied


def _classes(values: Iterable[str]) -> list[str]:
    return sorted(v[:CLASS_MAX] for v in values)[:POLICY_MAX]


def inputs(
    *,
    action_class: str,
    description: str,
    target: str | None,
    gated: Iterable[str],
    allowed: Iterable[str],
) -> dict[str, Any]:
    """The fields the Noul is asked with, cut to the catalogue's limits."""
    found: dict[str, Any] = {
        "action_class": action_class[:CLASS_MAX],
        "action_summary": (description.strip() or action_class)[:SUMMARY_MAX],
        "policy_gated": _classes(gated),
        "policy_allowed": _classes(allowed),
    }
    if target:
        found["target"] = target[:TARGET_MAX]
    return found


def read_answer(
    answers: Mapping[str, Any], *, fallback: bool, t_no: float | None
) -> ApprovalNeed | None:
    """The answer as `ApprovalNeed`; None when there is no Noul answer to read."""
    answer = answers.get(MAIN_QUESTION)
    p = getattr(answer, "noul", None)
    if not isinstance(p, (int, float)):
        return None
    threshold = 1.0 if t_no is None else min(max(1 - 2 * t_no, 0.0), 1.0)
    return ApprovalNeed(
        p=float(p), confidence=abs(float(p) - 0.5) * 2, fallback=fallback, threshold=threshold
    )
