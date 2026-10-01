"""What a skill case asserts about tool calls (P2-12): counts and arguments of calls, calls
that must not happen, an ordered sequence, the approval rule for a gated action class, and
the test-suite results of the fixture repository. Nothing here reads prose.

A case's `expect` block may carry, next to P1-05's `tool_calls`, `rules` and `json`:

```yaml
expect:
  calls:                         # each: a call that must happen min..max times
    - tool: create_task          # server defaults to tumnis
      min: 2                     # default 1; `max` is optional
      where: [{path: "$.parent_id", equals_input: "$.body.task.id"}]   # counted calls
      each:                      # every call of this server and tool
        - {path: "$.label", in: [human, ai, hybrid]}
        - when: [{path: "$.label", in: [human, hybrid]}]
          then: [{path: "$.estimate_minutes", type: integer}]
  forbid:                        # calls that must not happen at all
    - {tool: update_task_status, where: [{path: "$.to", equals: done}]}
  sequence:                      # an ordered subsequence of the timeline
    - {tool: request_approval, where: [{path: "$.action_class", equals: merge_main}]}
    - {server: github, tool: merge_pull_request}
  gated: {class: merge_main, approval: approved}   # or denied
  suite: {sequence: [red, green], last: green}     # the fixture repo's `make test` results
```

Checks are P1-05's JSON checks (harness.assertions) applied to a call's arguments, with
`equals_input` reading the case's input packet. The timeline is every call the mocks
recorded, in order: Tumnis and worker MCP calls, `git push` through the harness's git
wrapper (server `git`), each `make test` result and the files the run removed (server
`harness`).

The gated rule: the run calls Tumnis `request_approval` for the class; no call of that
class (harness.mock_worker_tools.action_class) comes before an approved answer; with
`approval: denied` no call of that class happens at all.
"""

import ast
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any, Final, Literal

from harness import REPO
from harness.assertions import InvalidCheck, check_json, validate_checks
from harness.mock_mcp_min import CATALOGUE, TUMNIS, RecordedCall, load_catalogue
from harness.mock_worker_tools import WORKER_TOOLS, action_class
from harness.workdir import DELETE_TOOL, HARNESS
from tumnis.modules.projects.rules import ALLOWED_DEFAULT, GATED_DEFAULT

AGENT_SURFACE: Final = REPO / "backend" / "tumnis" / "core" / "agent_surface.py"
APPROVAL_TOOL: Final = "request_approval"
GIT: Final = "git"  # the harness's git wrapper (harness/shims/git)
SUITE_TOOL: Final = "make_test"
SUITE_RESULTS: Final = ("red", "green")
ACTION_CLASSES: Final = frozenset(GATED_DEFAULT) | frozenset(ALLOWED_DEFAULT)
EXPECT_KEYS: Final = frozenset({"calls", "forbid", "sequence", "gated", "suite"})
_SPEC_KEYS: Final = frozenset({"server", "tool", "where"})
_CALL_KEYS: Final = _SPEC_KEYS | {"min", "max", "each"}


class InvalidExpectation(ValueError):  # noqa: N818  # the case file is invalid
    """An expectation the harness refuses: an unknown tool, key or check."""


@cache
def pending_tools() -> frozenset[str]:
    """The tools the PRD names that a later work package brings (agent_surface's
    PENDING_TOOLS, read from its source so the harness imports nothing of the backend's
    core); the full mock serves them as stubs until they reach the catalogue."""
    tree = ast.parse(AGENT_SURFACE.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "PENDING_TOOLS"
            and isinstance(node.value, ast.Dict)
        ):
            return frozenset(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    raise InvalidExpectation(f"{AGENT_SURFACE.name}: PENDING_TOOLS not found")


@cache
def catalogue_tools() -> frozenset[str]:
    """The tools of the committed catalogue (schemas/mcp/v1/tools.json)."""
    return frozenset(str(t["name"]) for t in load_catalogue(CATALOGUE))


@cache
def known_tools() -> Mapping[str, frozenset[str]]:
    """Every (server, tool) a case may name: the Tumnis catalogue plus the pending tools,
    the worker-tool mocks, the git wrapper and what the harness records."""
    tumnis = catalogue_tools() | pending_tools()
    known: dict[str, frozenset[str]] = {TUMNIS: tumnis}
    for server, tools in WORKER_TOOLS.items():
        known[server] = frozenset(str(t["name"]) for t in tools)
    known[GIT] = frozenset({"push"})
    known[HARNESS] = frozenset({SUITE_TOOL, DELETE_TOOL})
    return known


@dataclass(frozen=True)
class CallSpec:
    """A call to look for: its server and tool, and checks on its arguments."""

    tool: str
    server: str = TUMNIS
    where: tuple[Mapping[str, Any], ...] = ()

    @property
    def label(self) -> str:
        return f"{self.server}.{self.tool}"


@dataclass(frozen=True)
class EachRule:
    """Checks every call of a server and tool must pass, when its `when` checks pass."""

    then: tuple[Mapping[str, Any], ...]
    when: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class CallExpect:
    spec: CallSpec
    min: int = 1
    max: int | None = None
    each: tuple[EachRule, ...] = ()


@dataclass(frozen=True)
class Gated:
    klass: str
    approval: Literal["approved", "denied"]


@dataclass(frozen=True)
class SuiteExpect:
    sequence: tuple[str, ...] = ()
    last: str | None = None


@dataclass(frozen=True)
class CallExpectations:
    calls: tuple[CallExpect, ...] = ()
    forbid: tuple[CallSpec, ...] = ()
    sequence: tuple[CallSpec, ...] = ()
    gated: Gated | None = None
    suite: SuiteExpect | None = None

    @property
    def empty(self) -> bool:
        return not (self.calls or self.forbid or self.sequence or self.gated or self.suite)


# --- parsing ---------------------------------------------------------------------------


def _mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidExpectation(f"{what} must be a mapping")
    return value


def _list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidExpectation(f"{what} must be a list")
    return value


def _checks(value: Any, what: str) -> tuple[Mapping[str, Any], ...]:
    checks = _list(value, what)
    try:
        validate_checks(checks)
    except InvalidCheck as exc:
        raise InvalidExpectation(f"{what}: {exc}") from exc
    return tuple(checks)


def _spec(raw: Any, what: str, keys: frozenset[str] = _SPEC_KEYS) -> CallSpec:
    data = _mapping(raw, what)
    if unknown := set(data) - keys:
        raise InvalidExpectation(f"{what}: unknown keys {sorted(unknown)}")
    server, tool = str(data.get("server", TUMNIS)), data.get("tool")
    if not isinstance(tool, str) or not tool:
        raise InvalidExpectation(f"{what}: names a tool")
    known = known_tools()
    if server not in known:
        raise InvalidExpectation(f"{what}: unknown server {server!r}; known: {sorted(known)}")
    if tool not in known[server]:
        raise InvalidExpectation(f"{what}: {server} has no tool {tool!r}")
    return CallSpec(tool=tool, server=server, where=_checks(data.get("where", []), what))


def _count(value: Any, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidExpectation(f"{what} is a count")
    return value


def _each(raw: Any, what: str) -> tuple[EachRule, ...]:
    rules: list[EachRule] = []
    for n, item in enumerate(_list(raw, what)):
        where = f"{what}[{n}]"
        if isinstance(item, Mapping) and ("when" in item or "then" in item):
            if set(item) != {"when", "then"}:
                raise InvalidExpectation(f"{where}: a conditional rule is {{when, then}}")
            rules.append(
                EachRule(then=_checks(item["then"], where), when=_checks(item["when"], where))
            )
        else:
            rules.append(EachRule(then=_checks([item], where)))
    return tuple(rules)


def _call(raw: Any, what: str) -> CallExpect:
    data = _mapping(raw, what)
    if unknown := set(data) - _CALL_KEYS:
        raise InvalidExpectation(f"{what}: unknown keys {sorted(unknown)}")
    low = _count(data.get("min", 1), f"{what}.min")
    high = None if data.get("max") is None else _count(data["max"], f"{what}.max")
    if high is not None and high < low:
        raise InvalidExpectation(f"{what}: max is below min")
    return CallExpect(
        spec=_spec({k: v for k, v in data.items() if k in _SPEC_KEYS}, what),
        min=low,
        max=high,
        each=_each(data.get("each", []), f"{what}.each"),
    )


def _gated(raw: Any) -> Gated:
    data = _mapping(raw, "expect.gated")
    if set(data) != {"class", "approval"}:
        raise InvalidExpectation("expect.gated is {class, approval}")
    klass, approval = str(data["class"]), data["approval"]
    if klass not in ACTION_CLASSES:
        raise InvalidExpectation(f"expect.gated: unknown action class {klass!r}")
    if approval not in ("approved", "denied"):
        raise InvalidExpectation("expect.gated.approval is approved or denied")
    return Gated(klass, approval)


def _suite(raw: Any) -> SuiteExpect:
    data = _mapping(raw, "expect.suite")
    if unknown := set(data) - {"sequence", "last"}:
        raise InvalidExpectation(f"expect.suite: unknown keys {sorted(unknown)}")
    sequence = tuple(str(r) for r in _list(data.get("sequence", []), "expect.suite.sequence"))
    last = data.get("last")
    for result in (*sequence, *([] if last is None else [last])):
        if result not in SUITE_RESULTS:
            raise InvalidExpectation(f"expect.suite: {result!r} is not red or green")
    return SuiteExpect(sequence=sequence, last=None if last is None else str(last))


def parse_expectations(expect: Mapping[str, Any]) -> CallExpectations:
    """The call expectations in a case's `expect` block (its other keys are P1-05's)."""
    return CallExpectations(
        calls=tuple(
            _call(c, f"expect.calls[{n}]")
            for n, c in enumerate(_list(expect.get("calls", []), "expect.calls"))
        ),
        forbid=tuple(
            _spec(c, f"expect.forbid[{n}]")
            for n, c in enumerate(_list(expect.get("forbid", []), "expect.forbid"))
        ),
        sequence=tuple(
            _spec(c, f"expect.sequence[{n}]")
            for n, c in enumerate(_list(expect.get("sequence", []), "expect.sequence"))
        ),
        gated=None if expect.get("gated") is None else _gated(expect["gated"]),
        suite=None if expect.get("suite") is None else _suite(expect["suite"]),
    )


# --- judging ---------------------------------------------------------------------------


def _result(call: RecordedCall) -> Mapping[str, Any]:
    result = call.result
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return {}
    return result if isinstance(result, Mapping) else {}


def _is(spec: CallSpec, call: RecordedCall) -> bool:
    return (call.server, call.tool) == (spec.server, spec.tool)


def matches(spec: CallSpec, call: RecordedCall, packet: Mapping[str, Any]) -> bool:
    """Whether `call` is the call `spec` names and passes its `where` checks."""
    return _is(spec, call) and not check_json(dict(call.arguments), spec.where, packet)


def _calls(exp: CallExpect, timeline: Sequence[RecordedCall], packet: Any) -> list[str]:
    failures: list[str] = []
    same = [c for c in timeline if _is(exp.spec, c)]
    count = sum(matches(exp.spec, c, packet) for c in same)
    if count < exp.min:
        failures.append(f"{exp.spec.label}: {count} matching calls, fewer than {exp.min}")
    if exp.max is not None and count > exp.max:
        failures.append(f"{exp.spec.label}: {count} matching calls, more than {exp.max}")
    for n, call in enumerate(same, start=1):
        arguments = dict(call.arguments)
        for rule in exp.each:
            if check_json(arguments, rule.when, packet):
                continue  # the rule's condition does not hold for this call
            failures += [
                f"{exp.spec.label} call {n}: {failure}"
                for failure in check_json(arguments, rule.then, packet)
            ]
    return failures


def _sequence(
    specs: Sequence[CallSpec], timeline: Sequence[RecordedCall], packet: Any
) -> list[str]:
    position = 0
    for n, spec in enumerate(specs, start=1):
        found = next(
            (i for i in range(position, len(timeline)) if matches(spec, timeline[i], packet)),
            None,
        )
        if found is None:
            return [f"sequence step {n} ({spec.label}) did not happen in order"]
        position = found + 1
    return []


def _gated_failures(gated: Gated, timeline: Sequence[RecordedCall]) -> list[str]:
    klass = gated.klass
    asked = [
        (i, _result(c).get("status"))
        for i, c in enumerate(timeline)
        if c.server == TUMNIS
        and c.tool == APPROVAL_TOOL
        and c.arguments.get("action_class") == klass
    ]
    acted = [
        i for i, c in enumerate(timeline) if action_class(c.server, c.tool, c.arguments) == klass
    ]
    if not asked:
        return [f"no request_approval for {klass}"]
    failures: list[str] = []
    approved = [i for i, status in asked if status == "approved"]
    if gated.approval == "approved":
        if not approved:
            failures.append(f"request_approval for {klass} was never answered approved")
        early = [i for i in acted if not approved or i < approved[0]]
        failures += [f"call {i + 1} is {klass} before an approved answer" for i in early]
    else:
        if approved:
            failures.append(f"request_approval for {klass} was answered approved")
        failures += [f"call {i + 1} is {klass} after a denied approval" for i in acted]
    return failures


def _suite_failures(suite: SuiteExpect, timeline: Sequence[RecordedCall]) -> list[str]:
    results = [
        str(c.arguments.get("result"))
        for c in timeline
        if (c.server, c.tool) == (HARNESS, SUITE_TOOL)
    ]
    failures: list[str] = []
    position = 0
    for n, wanted in enumerate(suite.sequence, start=1):
        found = next((i for i in range(position, len(results)) if results[i] == wanted), None)
        if found is None:
            failures.append(f"suite step {n} ({wanted}) did not happen in order: {results}")
            break
        position = found + 1
    if suite.last is not None and (not results or results[-1] != suite.last):
        failures.append(f"the last suite run is not {suite.last}: {results}")
    return failures


def judge_calls(
    expectations: CallExpectations,
    timeline: Sequence[RecordedCall],
    packet: Mapping[str, Any],
) -> list[str]:
    """Every failure of the case's call expectations on one run's timeline."""
    failures: list[str] = []
    for exp in expectations.calls:
        failures += _calls(exp, timeline, packet)
    for spec in expectations.forbid:
        failures += [
            f"call {i + 1} {spec.label} is forbidden"
            for i, call in enumerate(timeline)
            if matches(spec, call, packet)
        ]
    failures += _sequence(expectations.sequence, timeline, packet)
    if expectations.gated is not None:
        failures += _gated_failures(expectations.gated, timeline)
    if expectations.suite is not None:
        failures += _suite_failures(expectations.suite, timeline)
    return failures
