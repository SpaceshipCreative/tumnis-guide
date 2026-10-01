"""Skill case files (P1-05): one YAML file per case under profiles/tests/cases/<skill>/.

```yaml
id: enrich-hybrid-invoice
profile: project-template            # directory under profiles/
skill: enrich
input: ../../recordings/enrich/hybrid_invoice.packet.json   # a recorded TaskPacket
output_schema: {family: enrichment, name: result, version: 1}
meta: {test_id: T-P1-05-01, req: [FR-5.2], wp: P1-05, xfail: "spec:P1-05"}   # optional
expect:
  tool_calls: {allow: []}            # name globs; phase 1: any tool call fails the case
  rules: [enrichment_errors]         # named pure checks (assertions.RULES)
  json: [{path: "$.task_id", equals_input: "$.body.task.id"}]
```

The number of runs is not a case's to choose: harness.toml fixes it (three), and a case
that sets `runs` is refused when it loads.

P2-12 adds skills that call tools. Their cases run against the full mock Tumnis MCP
server (harness.mock_mcp), the worker-tool mocks and the memory mock, scripted by a `mock`
block, and assert on the calls (harness.calls: `calls`, `forbid`, `sequence`, `gated`,
`suite`):

```yaml
output_schema: {tool: post_result}   # the reply is post_result's input, less run_id and
                                     # idempotency_key; or {family: harness, name, version}
                                     # for profiles/harness/schemas/<name>.v<version>.json
mock:
  responses:                         # per Tumnis tool; unlisted tools answer from the seed
    create_task: {from: seed}        # the mock assigns an id and echoes the task
    request_approval: {default: approved}   # approved | denied | pending | answered
    get_project_digest:              # a list answers call by call (the last one repeats)
      - {result: {scope: project, entries: [], next_cursor: c-2, has_more: true}}
  recall: ["Tumnis digest cursor: c-1"]   # what the memory mock's recall returns
  repo: calc                         # the run's working directory: a copy of
                                     # tests/fixtures/repos/<repo> as a fresh git repository
  hidden_failure: true               # `make test` also runs a test that cannot pass
```

A case with call expectations and no `tool_calls` allows every tool call.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import ValidationError

from harness import REPO
from harness.assertions import RULE_REQUESTS, RULES, InvalidCheck, validate_checks
from harness.calls import EXPECT_KEYS as CALL_KEYS
from harness.calls import (
    CallExpectations,
    InvalidExpectation,
    catalogue_tools,
    known_tools,
    parse_expectations,
)
from harness.mock_mcp_min import TUMNIS

CASE_KEYS: Final = frozenset(
    {"id", "profile", "skill", "input", "output_schema", "meta", "expect", "mock"}
)
EXPECT_KEYS: Final = frozenset({"tool_calls", "rules", "json"}) | CALL_KEYS
META_KEYS: Final = frozenset({"test_id", "req", "wp", "xfail"})
MOCK_KEYS: Final = frozenset({"responses", "recall", "repo", "hidden_failure"})
APPROVAL_STATUSES: Final = ("pending", "answered", "approved", "denied")
SUITE_DIR: Final = "hostile"  # tests/cases/hostile/ holds the hostile suite's index (P2-11)
TOOL_FAMILY: Final = "tool"  # an output schema taken from a catalogue tool's input (P2-12)
HARNESS_FAMILY: Final = "harness"  # an output schema the harness owns (P2-12)
HARNESS_SCHEMAS: Final = "profiles/harness/schemas"
REPOS: Final = REPO / "profiles" / "tests" / "fixtures" / "repos"
_ID: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
_NAME: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")  # the runner protocol's NAME_RE
_GLOB: Final = re.compile(r"^[A-Za-z0-9_.*?-]{1,128}$")


class CaseError(ValueError):
    """A case file the harness refuses to run."""


@dataclass(frozen=True)
class SchemaName:
    family: str
    name: str
    version: int

    @property
    def path(self) -> str:
        """The committed JSON Schema, relative to the repository root: a generated one, a
        catalogue tool's input (`schemas/mcp/v1/tools.json#<tool>`), or the harness's."""
        if self.family == TOOL_FAMILY:
            return f"schemas/mcp/v1/tools.json#{self.name}"
        if self.family == HARNESS_FAMILY:
            return f"{HARNESS_SCHEMAS}/{self.name}.v{self.version}.json"
        return f"schemas/{self.family}/v{self.version}/{self.name}.json"


@dataclass(frozen=True)
class CaseMeta:
    """Traceability for the pytest item (test id, requirement and WP tags) and the
    `spec:` expected-failure reason while the case is waiting for its skill."""

    test_id: str | None = None
    req: tuple[str, ...] = ()
    wp: str | None = None
    xfail: str | None = None


@dataclass(frozen=True)
class MockSpec:
    """How the mocks answer one case (P2-12): Tumnis tool responses, each a sequence of
    `{from: seed}`, `{default: <approval status>}` or `{result: {...}}` (the last one
    repeats), what memory's recall returns, and the fixture repository the run works in."""

    responses: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    recall: tuple[str, ...] = ()
    repo: str | None = None
    hidden_failure: bool = False


@dataclass(frozen=True)
class Case:
    id: str
    path: Path
    profile: str
    skill: str
    input_path: Path
    packet: dict[str, Any]
    output_schema: SchemaName
    allow: tuple[str, ...]
    rules: tuple[str, ...]
    json_checks: tuple[dict[str, Any], ...]
    meta: CaseMeta
    mock: MockSpec = field(default_factory=MockSpec)
    expectations: CallExpectations = field(default_factory=CallExpectations)

    @property
    def uses_mocks(self) -> bool:
        """Whether the case runs against the mock servers (a P2-12 tool-calling skill)."""
        return not self.expectations.empty or self.mock != MockSpec()


def _mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CaseError(f"{what} must be a mapping")
    return value


def _strings(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise CaseError(f"{what} must be a list of strings")
    return tuple(value)


def _meta(raw: Any) -> CaseMeta:
    if raw is None:
        return CaseMeta()
    data = _mapping(raw, "meta")
    if unknown := set(data) - META_KEYS:
        raise CaseError(f"meta: unknown keys {sorted(unknown)}")
    xfail = data.get("xfail")
    if xfail is not None and not str(xfail).startswith("spec:"):
        raise CaseError("meta.xfail is a spec reason, 'spec:<WP>'")
    return CaseMeta(
        test_id=None if data.get("test_id") is None else str(data["test_id"]),
        req=_strings(data.get("req", []), "meta.req"),
        wp=None if data.get("wp") is None else str(data["wp"]),
        xfail=None if xfail is None else str(xfail),
    )


def _packet(path: Path) -> dict[str, Any]:
    try:
        packet = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CaseError(f"input {path.name}: not a readable JSON file") from exc
    if not isinstance(packet, dict) or not isinstance(packet.get("prompt_text"), str):
        raise CaseError(f"input {path.name}: a recorded TaskPacket carries prompt_text")
    if not isinstance(packet.get("body"), dict):
        raise CaseError(f"input {path.name}: a recorded TaskPacket carries a body")
    return packet


def _check_body(name: str, rules: tuple[str, ...], body: dict[str, Any]) -> None:
    """Each rule reads the body as its request model; a body it cannot read is the case's
    fault, so it is refused here rather than failing a reply later."""
    for rule in rules:
        try:
            RULE_REQUESTS[rule].model_validate(body)
        except ValidationError as exc:
            raise CaseError(
                f"{name}: the input's body is not a request {rule} can read "
                f"({exc.error_count()} errors)"
            ) from exc


def _output_schema(raw: Any, name: str) -> SchemaName:
    schema = _mapping(raw, "output_schema")
    if set(schema) == {"tool"}:
        tool = str(schema["tool"])
        if tool not in catalogue_tools():
            raise CaseError(f"{name}: output_schema names {tool!r}, not a catalogue tool")
        return SchemaName(TOOL_FAMILY, tool, 1)
    try:
        found = SchemaName(str(schema["family"]), str(schema["name"]), int(schema["version"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise CaseError(f"{name}: output_schema is {{family, name, version}} or {{tool}}") from exc
    if found.family == HARNESS_FAMILY and not (REPO / found.path).is_file():
        raise CaseError(f"{name}: output_schema {found.path} does not exist")
    return found


def _response(raw: Any, what: str) -> Mapping[str, Any]:
    if isinstance(raw, dict) and len(raw) == 1:
        ((key, value),) = raw.items()
        ok = (
            (key == "from" and value == "seed")
            or (key == "default" and value in APPROVAL_STATUSES)
            or (key == "result" and isinstance(value, dict))
        )
        if ok:
            return raw
    raise CaseError(f"{what}: a response is {{from: seed}}, {{default: <status>}} or {{result}}")


def _mock(raw: Any, name: str) -> MockSpec:
    if raw is None:
        return MockSpec()
    data = _mapping(raw, "mock")
    if unknown := set(data) - MOCK_KEYS:
        raise CaseError(f"{name}: mock has unknown keys {sorted(unknown)}")
    responses: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for tool, spec in _mapping(data.get("responses", {}), "mock.responses").items():
        if tool not in known_tools()[TUMNIS]:
            raise CaseError(f"{name}: mock.responses names an unknown Tumnis tool {tool!r}")
        specs = spec if isinstance(spec, list) else [spec]
        if not specs:
            raise CaseError(f"{name}: mock.responses.{tool} is empty")
        responses[str(tool)] = tuple(
            _response(s, f"{name}: mock.responses.{tool}[{n}]") for n, s in enumerate(specs)
        )
    repo = data.get("repo")
    if repo is not None and not (_NAME.fullmatch(str(repo)) and (REPOS / str(repo)).is_dir()):
        raise CaseError(f"{name}: mock.repo {repo!r} is not under tests/fixtures/repos")
    hidden = data.get("hidden_failure", False)
    if not isinstance(hidden, bool) or (hidden and repo is None):
        raise CaseError(f"{name}: mock.hidden_failure is a boolean, for a case with a repo")
    return MockSpec(
        responses=responses,
        recall=_strings(data.get("recall", []), "mock.recall"),
        repo=None if repo is None else str(repo),
        hidden_failure=hidden,
    )


def load_case(path: Path) -> Case:
    """The case in `path`; CaseError when it is malformed, sets `runs`, names an unknown
    rule, operator or tool, or its input is not a recorded packet."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CaseError(f"{path.name}: not a readable YAML file") from exc
    data = _mapping(raw, path.name)
    if "runs" in data:
        raise CaseError(
            f"{path.name}: a case cannot set runs; harness.toml runs every case 3 times"
        )
    if unknown := set(data) - CASE_KEYS:
        raise CaseError(f"{path.name}: unknown keys {sorted(unknown)}")
    if missing := {"id", "profile", "skill", "input", "output_schema", "expect"} - set(data):
        raise CaseError(f"{path.name}: missing {sorted(missing)}")

    case_id, profile, skill = str(data["id"]), str(data["profile"]), str(data["skill"])
    if not _ID.fullmatch(case_id):
        raise CaseError(f"{path.name}: id {case_id!r} is not lower-case-with-dashes")
    if not _NAME.fullmatch(profile) or not _NAME.fullmatch(skill):
        raise CaseError(f"{path.name}: profile and skill are plain names")

    output_schema = _output_schema(data["output_schema"], path.name)

    expect = _mapping(data["expect"], "expect")
    if unknown := set(expect) - EXPECT_KEYS:
        raise CaseError(f"{path.name}: expect has unknown keys {sorted(unknown)}")
    try:
        expectations = parse_expectations(expect)
    except InvalidExpectation as exc:
        raise CaseError(f"{path.name}: {exc}") from exc
    default_allow = {"allow": [] if expectations.empty else ["*"]}
    tool_calls = _mapping(expect.get("tool_calls", default_allow), "expect.tool_calls")
    allow = _strings(tool_calls.get("allow", []), "expect.tool_calls.allow")
    if bad := [g for g in allow if not _GLOB.fullmatch(g)]:
        raise CaseError(f"{path.name}: tool call globs {bad} are not tool names")
    rules = _strings(expect.get("rules", []), "expect.rules")
    if unknown_rules := [r for r in rules if r not in RULES]:
        raise CaseError(f"{path.name}: unknown rules {unknown_rules}; known: {sorted(RULES)}")
    checks = expect.get("json", [])
    if not isinstance(checks, list):
        raise CaseError(f"{path.name}: expect.json must be a list of checks")
    try:
        validate_checks(checks)
    except InvalidCheck as exc:
        raise CaseError(f"{path.name}: {exc}") from exc

    input_path = (path.parent / str(data["input"])).resolve()
    packet = _packet(input_path)
    _check_body(path.name, rules, packet["body"])
    return Case(
        id=case_id,
        path=path,
        profile=profile,
        skill=skill,
        input_path=input_path,
        packet=packet,
        output_schema=output_schema,
        allow=allow,
        rules=rules,
        json_checks=tuple(checks),
        meta=_meta(data.get("meta")),
        mock=_mock(data.get("mock"), path.name),
        expectations=expectations,
    )


def is_suite_file(path: Path) -> bool:
    """A file of a suite's own folder (`<cases>/hostile/`, P2-11), which holds the suite's
    generated index rather than skill cases."""
    return SUITE_DIR in path.parent.parts


def case_gaps(cases: Sequence[Case], skills: Iterable[tuple[str, str]]) -> list[str]:
    """Every (profile, skill) with no case of its own in `<cases>/<skill>/` (P2-12: the
    Skills job covers every skill), and every case in a folder not named for its skill."""
    gaps = [
        f"{profile}/{skill}: no case"
        for profile, skill in skills
        if not any((c.profile, c.skill) == (profile, skill) for c in cases)
    ]
    gaps += [
        f"case {c.id}: in {c.path.parent.name}/, not {c.skill}/"
        for c in cases
        if c.path.parent.name != c.skill
    ]
    return gaps


def load_cases(root: Path) -> list[Case]:
    """Every case under `root` (`*.yaml`, recursively, outside the suite folders), sorted
    by path; ids are unique."""
    paths = [p for p in sorted(root.rglob("*.yaml")) if not is_suite_file(p.relative_to(root))]
    cases = [load_case(p) for p in paths]
    seen: dict[str, Path] = {}
    for case in cases:
        if case.id in seen:
            raise CaseError(f"case id {case.id!r} is in both {seen[case.id]} and {case.path}")
        seen[case.id] = case.path
    return cases
