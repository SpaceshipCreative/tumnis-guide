"""Skill case assertions are on JSON only: a closed set of operators, none of which reads
prose (P1-05, PRD Quality: Hermes skills)."""

from __future__ import annotations

from typing import Any

import pytest

OUTPUT: dict[str, Any] = {
    "task_id": "t-17",
    "label": "human",
    "estimate_minutes": 30,
    "flag": True,
    "note": None,
    "criteria": ["The invoice is sent", "Acme confirms receipt"],
    "split": {"ai_portion": "", "human_portion": "Sign and send it"},
    "picks": [
        {"task_id": "a", "reason": "Due today"},
        {"task_id": "b", "reason": ""},
    ],
}
INPUT: dict[str, Any] = {"body": {"task": {"id": "t-17"}, "candidates": ["a", "b"]}}

# case -> (check, True: passes / False: fails / None: refused when the case loads)
CASES: dict[str, tuple[dict[str, Any], bool | None]] = {
    "equals_pass": ({"path": "$.label", "equals": "human"}, True),
    "equals_fail": ({"path": "$.label", "equals": "ai"}, False),
    "equals_input_pass": ({"path": "$.task_id", "equals_input": "$.body.task.id"}, True),
    "equals_input_fail": ({"path": "$.label", "equals_input": "$.body.task.id"}, False),
    "type_integer_pass": ({"path": "$.estimate_minutes", "type": "integer"}, True),
    "type_integer_fail": ({"path": "$.label", "type": "integer"}, False),
    "type_bool_is_not_integer": ({"path": "$.flag", "type": "integer"}, False),
    "type_object_pass": ({"path": "$.split", "type": "object"}, True),
    "between_pass": ({"path": "$.estimate_minutes", "between": [5, 480]}, True),
    "between_fail": ({"path": "$.estimate_minutes", "between": [45, 480]}, False),
    "between_not_a_number": ({"path": "$.label", "between": [5, 480]}, False),
    "nonempty_pass": ({"path": "$.split.human_portion", "nonempty": True}, True),
    "nonempty_fail": ({"path": "$.split.ai_portion", "nonempty": True}, False),
    "min_items_pass": ({"path": "$.criteria", "min_items": 1}, True),
    "min_items_fail": ({"path": "$.criteria", "min_items": 3}, False),
    "max_items_pass": ({"path": "$.criteria", "max_items": 2}, True),
    "max_items_fail": ({"path": "$.criteria", "max_items": 1}, False),
    "in_pass": ({"path": "$.label", "in": ["human", "hybrid"]}, True),
    "in_fail": ({"path": "$.label", "in": ["ai"]}, False),
    "absent_pass_when_missing": ({"path": "$.hybrid_split", "absent": True}, True),
    "absent_pass_when_null": ({"path": "$.note", "absent": True}, True),
    "absent_fail": ({"path": "$.estimate_minutes", "absent": True}, False),
    "matches_pass": ({"path": "$.task_id", "matches": "t-[0-9]+"}, True),
    "matches_fail": ({"path": "$.label", "matches": "[0-9]+"}, False),
    "each_pass": ({"path": "$.picks", "each": [{"path": "$.task_id", "in": ["a", "b"]}]}, True),
    "each_fail": ({"path": "$.picks", "each": [{"path": "$.reason", "nonempty": True}]}, False),
    "index_path": ({"path": "$.criteria[1]", "equals": "Acme confirms receipt"}, True),
    "missing_path_fails": ({"path": "$.first_action", "equals": "x"}, False),
    "prose_operator_refused": ({"path": "$.picks[0].reason", "contains": "today"}, None),
    "no_operator_refused": ({"path": "$.label"}, None),
    "bad_path_refused": ({"path": "label", "equals": "human"}, None),
}


@pytest.mark.req("Quality: Hermes skills")
@pytest.mark.wp("P1-05")
@pytest.mark.parametrize("case", sorted(CASES))
def test_operators(case: str) -> None:
    """T-P1-05-06
    Each operator passes and fails on crafted JSON; the operator set is closed and has
    no operator that inspects prose, so a check naming anything else is refused.
    """
    from harness.assertions import OPERATORS, InvalidCheck, check_json, validate_checks

    assert OPERATORS == frozenset(
        {
            "equals",
            "equals_input",
            "type",
            "between",
            "nonempty",
            "min_items",
            "max_items",
            "in",
            "absent",
            "matches",
            "each",
        }
    )

    check, passes = CASES[case]
    if passes is None:
        with pytest.raises(InvalidCheck):
            validate_checks([check])
        return
    validate_checks([check])
    failures = check_json(OUTPUT, [check], INPUT)
    assert (failures == []) is passes, failures
    assert all(isinstance(f, str) and f.startswith(check["path"]) for f in failures)
