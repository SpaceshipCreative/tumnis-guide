"""What a skill case asserts: JSON checks from a closed set of operators, and named pure
rules from the backend (P1-05). Nothing here reads prose.

A check is a mapping with a `path` and one or more operators:

    {path: "$.estimate_minutes", type: integer, between: [5, 480]}

Paths are a small JSONPath subset: `$`, then `.key` and `[index]` steps. A check fails when
its path does not resolve, except `absent`, which passes when the path is missing or null.
`each` applies a nested list of checks to every element of an array, with `$` bound to
the element. `equals_input` compares with a path into the case's input packet.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

from tumnis.modules.agents.rules import enrichment_errors, planning_errors
from tumnis.modules.agents.skill_io import (
    EnrichmentRequest,
    EnrichmentResult,
    PlanningRequest,
    PlanningResult,
)

OPERATORS: Final = frozenset(
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
JSON_TYPES: Final = frozenset({"string", "integer", "number", "boolean", "array", "object", "null"})
_STEP: Final = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")
_MISSING: Final = object()

Check = Mapping[str, Any]


class InvalidCheck(ValueError):  # noqa: N818  # the case file is invalid, not the reply
    """A check that names an unknown operator, has no operator, or a bad argument."""


def _steps(path: str) -> list[str | int]:
    if not path.startswith("$"):
        raise InvalidCheck(f"{path!r}: a path starts with $")
    steps: list[str | int] = []
    rest = path[1:]
    while rest:
        match = _STEP.match(rest)
        if match is None:
            raise InvalidCheck(f"{path!r}: only .key and [index] steps are supported")
        key, index = match.groups()
        steps.append(key if key is not None else int(index))
        rest = rest[match.end() :]
    return steps


def resolve(doc: Any, path: str) -> Any:
    """The value at `path`, or the module's missing marker when the path does not resolve."""
    value = doc
    for step in _steps(path):
        if isinstance(step, int):
            if not isinstance(value, list) or step >= len(value):
                return _MISSING
            value = value[step]
        else:
            if not isinstance(value, dict) or step not in value:
                return _MISSING
            value = value[step]
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _json_type(value: Any) -> str:  # noqa: PLR0911
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object" if isinstance(value, dict) else type(value).__name__


def _validate_argument(op: str, arg: Any, where: str) -> None:  # noqa: PLR0912
    if op == "type":
        if arg not in JSON_TYPES:
            raise InvalidCheck(f"{where}: type is one of {sorted(JSON_TYPES)}")
    elif op == "between":
        ok = isinstance(arg, list) and len(arg) == 2 and all(_is_number(a) for a in arg)  # noqa: PLR2004
        if not ok or arg[0] > arg[1]:
            raise InvalidCheck(f"{where}: between takes [low, high]")
    elif op in ("nonempty", "absent"):
        if arg is not True:
            raise InvalidCheck(f"{where}: {op} takes true")
    elif op in ("min_items", "max_items"):
        if not isinstance(arg, int) or isinstance(arg, bool) or arg < 0:
            raise InvalidCheck(f"{where}: {op} takes a count")
    elif op == "in":
        if not isinstance(arg, list) or not arg:
            raise InvalidCheck(f"{where}: in takes a non-empty list")
    elif op == "matches":
        try:
            re.compile(str(arg))
        except re.error as exc:
            raise InvalidCheck(f"{where}: matches takes a regular expression") from exc
    elif op == "equals_input":
        _steps(str(arg))
    elif op == "each":
        if not isinstance(arg, list) or not arg:
            raise InvalidCheck(f"{where}: each takes a non-empty list of checks")
        validate_checks(arg)


def validate_checks(checks: Sequence[Any]) -> None:
    """Refuses a check list that is not a list of {path, operator...} with known
    operators and well-formed arguments (InvalidCheck)."""
    for check in checks:
        if not isinstance(check, Mapping) or not isinstance(check.get("path"), str):
            raise InvalidCheck(f"{check!r}: a check is a mapping with a path")
        path = check["path"]
        _steps(path)
        ops = set(check) - {"path"}
        if not ops:
            raise InvalidCheck(f"{path}: a check needs at least one operator")
        if unknown := ops - OPERATORS:
            raise InvalidCheck(f"{path}: unknown operator(s) {sorted(unknown)}")
        for op in sorted(ops):
            _validate_argument(op, check[op], path)


def _apply(op: str, arg: Any, value: Any, input_doc: Any) -> str | None:  # noqa: PLR0911, PLR0912
    """None when `value` satisfies the operator, else what is wrong."""
    if op == "equals":
        return None if value == arg else f"expected {arg!r}, got {value!r}"
    if op == "equals_input":
        expected = resolve(input_doc, arg)
        if expected is _MISSING:
            return f"the input has nothing at {arg}"
        return None if value == expected else f"expected the input's {arg} {expected!r}"
    if op == "type":
        actual = _json_type(value)
        ok = actual == arg or (arg == "number" and actual == "integer")
        return None if ok else f"expected {arg}, got {actual}"
    if op == "between":
        if not _is_number(value):
            return f"expected a number, got {_json_type(value)}"
        return None if arg[0] <= value <= arg[1] else f"{value} is not in [{arg[0]}, {arg[1]}]"
    if op == "nonempty":
        empty = value is None or (isinstance(value, str) and not value.strip())
        empty = empty or (isinstance(value, list | dict) and not value)
        return "is empty" if empty else None
    if op in ("min_items", "max_items"):
        if not isinstance(value, list):
            return f"expected an array, got {_json_type(value)}"
        if op == "min_items" and len(value) < arg:
            return f"has {len(value)} items, fewer than {arg}"
        if op == "max_items" and len(value) > arg:
            return f"has {len(value)} items, more than {arg}"
        return None
    if op == "in":
        return None if value in arg else f"{value!r} is not one of {arg!r}"
    if op == "matches":
        if not isinstance(value, str):
            return f"expected a string, got {_json_type(value)}"
        return None if re.fullmatch(str(arg), value) else f"{value!r} does not match {arg!r}"
    raise InvalidCheck(f"unknown operator {op!r}")


def check_json(output: Any, checks: Sequence[Check], input_doc: Any) -> list[str]:
    """Every failure of `checks` on `output`, as "<path>: <what is wrong>"; [] when all
    pass. `input_doc` is the case's input packet, for `equals_input`."""
    failures: list[str] = []
    for check in checks:
        path = check["path"]
        value = resolve(output, path)
        for op in sorted(set(check) - {"path"}):
            arg = check[op]
            if op == "absent":
                if value is not _MISSING and value is not None:
                    failures.append(f"{path}: expected no value, got {value!r}")
            elif value is _MISSING:
                failures.append(f"{path}: missing")
                break
            elif op == "each":
                if not isinstance(value, list):
                    failures.append(f"{path}: expected an array, got {_json_type(value)}")
                    continue
                for i, element in enumerate(value):
                    failures += [
                        f"{path}[{i}]{failure[1:]}"
                        for failure in check_json(element, arg, input_doc)
                    ]
            elif (problem := _apply(op, arg, value, input_doc)) is not None:
                failures.append(f"{path}: {problem}")
    return failures


# --- named rules ------------------------------------------------------------------------

Rule = Callable[[Mapping[str, Any], Mapping[str, Any]], list[str]]


def _enrichment(body: Mapping[str, Any], output: Mapping[str, Any]) -> list[str]:
    req = EnrichmentRequest.model_validate(body)
    return enrichment_errors(req, EnrichmentResult.model_validate(output))


def _planning(body: Mapping[str, Any], output: Mapping[str, Any]) -> list[str]:
    req = PlanningRequest.model_validate(body)
    return planning_errors(req, PlanningResult.model_validate(output))


# name -> fn(packet body, reply) -> error codes; the pure rules P1-08 and P1-11 apply too.
RULES: Final[Mapping[str, Rule]] = {
    "enrichment_errors": _enrichment,
    "planning_errors": _planning,
}
