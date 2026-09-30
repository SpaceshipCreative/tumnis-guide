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
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from harness.assertions import RULES, InvalidCheck, validate_checks

CASE_KEYS: Final = frozenset({"id", "profile", "skill", "input", "output_schema", "meta", "expect"})
EXPECT_KEYS: Final = frozenset({"tool_calls", "rules", "json"})
META_KEYS: Final = frozenset({"test_id", "req", "wp", "xfail"})
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
        """The committed JSON Schema, relative to the repository root."""
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


def load_case(path: Path) -> Case:
    """The case in `path`; CaseError when it is malformed, sets `runs`, names an unknown
    rule or operator, or its input is not a recorded packet."""
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

    schema = _mapping(data["output_schema"], "output_schema")
    try:
        output_schema = SchemaName(
            str(schema["family"]), str(schema["name"]), int(schema["version"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CaseError(f"{path.name}: output_schema is {{family, name, version}}") from exc

    expect = _mapping(data["expect"], "expect")
    if unknown := set(expect) - EXPECT_KEYS:
        raise CaseError(f"{path.name}: expect has unknown keys {sorted(unknown)}")
    tool_calls = _mapping(expect.get("tool_calls", {"allow": []}), "expect.tool_calls")
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
    return Case(
        id=case_id,
        path=path,
        profile=profile,
        skill=skill,
        input_path=input_path,
        packet=_packet(input_path),
        output_schema=output_schema,
        allow=allow,
        rules=rules,
        json_checks=tuple(checks),
        meta=_meta(data.get("meta")),
    )


def load_cases(root: Path) -> list[Case]:
    """Every case under `root` (`*.yaml`, recursively), sorted by path; ids are unique."""
    cases = [load_case(p) for p in sorted(root.rglob("*.yaml"))]
    seen: dict[str, Path] = {}
    for case in cases:
        if case.id in seen:
            raise CaseError(f"case id {case.id!r} is in both {seen[case.id]} and {case.path}")
        seen[case.id] = case.path
    return cases
