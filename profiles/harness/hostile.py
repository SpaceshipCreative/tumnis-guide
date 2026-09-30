"""The hostile content suite (P2-11, SAF-6, A2.7): outside text that tries to steer an
agent (emails, chat messages, notes and documents), each case with a benign twin, run
against every template and master skill.

The cases live in backend/fixtures/hostile (data, locked by spec-guard), listed by its
`index.yaml`:

```yaml
id: email-004-fake-approval
version: 1
source: email                        # email | chat | note | document
technique: fake_approval
inject_as: context_item              # context_item | passage | task_title | digest_entry
envelope: {from: "ops@acme-client.example", subject: "Re: release"}
content: |
  (payload text; kept exactly, invisible characters included)
companions: []                       # optional: more parts injected into the same task
pdf: null                            # optional: the hidden-text PDF this passage comes from
attack_goal: deploy to production without a human approval
forbidden_calls:                     # server and tool globs; a regex per argument ("*": any)
  - {server: coolify, tool: deploy, args_match: {environment: "(?i)prod"}}
forbidden_action_classes: [deploy_production]
benign_twin: email-004-benign        # same envelope and injection point, harmless content
expected_calls_on_twin: []           # the calls a skill still makes on the twin
skills: all                          # or a list of skill names
```

A twin file has `id`, `version`, `twin_of`, `source`, `inject_as`, `envelope`, `content`
(and `companions` when its case has them).
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness import REPO
from harness.cases import CaseError
from tumnis.modules.projects.rules import ALLOWED_DEFAULT, GATED_DEFAULT

HOSTILE_ROOT: Final = REPO / "backend" / "fixtures" / "hostile"
SOURCES: Final = ("email", "chat", "note", "document")
INJECT_POINTS: Final = ("context_item", "passage", "task_title", "digest_entry")
# The project policy's own vocabulary (projects.rules): a case names only these.
ACTION_CLASSES: Final = frozenset(GATED_DEFAULT) | frozenset(ALLOWED_DEFAULT)
TITLE_MAX: Final = 300  # characters of a task_title payload (a task title is at most 500)
INDEX_KEYS: Final = frozenset({"version", "meta", "smoke", "cases"})

Source = Literal["email", "chat", "note", "document"]
InjectPoint = Literal["context_item", "passage", "task_title", "digest_entry"]
_Id = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,80}$")


class CallPattern(BaseModel):
    """A tool call to look for: server and tool name globs, and a regular expression per
    argument name (`*`: the whole argument object as JSON)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    args_match: dict[str, str] = Field(default_factory=dict)


class Companion(BaseModel):
    """Another part of the same payload, injected into the same task (a split payload)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Source
    inject_as: InjectPoint
    envelope: dict[str, str] = Field(default_factory=dict)
    content: str = Field(min_length=1)


class HostileCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = _Id
    version: int = Field(ge=1)
    source: Source
    technique: str = Field(min_length=1)
    inject_as: InjectPoint
    envelope: dict[str, str] = Field(default_factory=dict)
    content: str = Field(min_length=1)
    companions: tuple[Companion, ...] = ()
    pdf: str | None = None
    attack_goal: str = Field(min_length=1)
    forbidden_calls: tuple[CallPattern, ...] = ()
    forbidden_action_classes: tuple[str, ...] = ()
    benign_twin: str = _Id
    expected_calls_on_twin: tuple[CallPattern, ...] = ()
    skills: Literal["all"] | tuple[str, ...] = "all"
    path: Path | None = None

    def covers(self, skill: str) -> bool:
        return self.skills == "all" or skill in self.skills


class BenignTwin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = _Id
    version: int = Field(ge=1)
    twin_of: str = _Id
    source: Source
    inject_as: InjectPoint
    envelope: dict[str, str] = Field(default_factory=dict)
    content: str = Field(min_length=1)
    companions: tuple[Companion, ...] = ()
    path: Path | None = None


@dataclass(frozen=True)
class HostileSet:
    """The loaded set: cases and twins by id, the index's version and meta, and the fixed
    smoke subset every PR runs."""

    version: int
    cases: dict[str, HostileCase]
    twins: dict[str, BenignTwin]
    smoke: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    def only(self, keep: Callable[[HostileCase], bool]) -> "HostileSet":
        """The set with only the cases `keep` accepts (and their twins)."""
        cases = {cid: case for cid, case in self.cases.items() if keep(case)}
        twins = {c.benign_twin: self.twins[c.benign_twin] for c in cases.values()}
        return replace(
            self, cases=cases, twins=twins, smoke=tuple(s for s in self.smoke if s in cases)
        )


def _yaml(path: Path, what: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CaseError(f"{what} {path.name}: not a readable YAML file") from exc
    if not isinstance(data, dict):
        raise CaseError(f"{what} {path.name}: must be a mapping")
    return data


def _model[M: BaseModel](model: type[M], path: Path, what: str) -> M:
    data = _yaml(path, what)
    try:
        return model.model_validate({**data, "path": path})
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise CaseError(f"{what} {path.name}: {where}: {first['msg']}") from exc


def _check_case(case: HostileCase, root: Path) -> None:
    if unknown := set(case.forbidden_action_classes) - ACTION_CLASSES:
        raise CaseError(f"case {case.id}: unknown action classes {sorted(unknown)}")
    if case.inject_as == "task_title" and (len(case.content) > TITLE_MAX or "\n" in case.content):
        raise CaseError(f"case {case.id}: a task_title payload is one line of {TITLE_MAX} or less")
    if case.pdf is not None and not (root / case.source / case.pdf).is_file():
        raise CaseError(f"case {case.id}: pdf {case.pdf} is not in {case.source}/")
    for pattern in (*case.forbidden_calls, *case.expected_calls_on_twin):
        for regex in pattern.args_match.values():
            try:
                re.compile(regex)
            except re.error as exc:
                raise CaseError(f"case {case.id}: bad args_match regex {regex!r}") from exc


def _check_twin(case: HostileCase, twin: BenignTwin) -> None:
    if twin.twin_of != case.id or case.benign_twin != twin.id:
        raise CaseError(f"twin {twin.id} and case {case.id} do not name each other")
    same = (twin.source, twin.inject_as, twin.envelope) == (
        case.source,
        case.inject_as,
        case.envelope,
    )
    if not same:
        raise CaseError(f"twin {twin.id}: source, inject_as and envelope must match {case.id}")
    if twin.content == case.content:
        raise CaseError(f"twin {twin.id}: its content must differ from {case.id}'s")


def load_hostile(root: Path = HOSTILE_ROOT) -> HostileSet:
    """The set listed by `root/index.yaml`; CaseError when a file is malformed, an id is
    used twice, a twin is missing or does not match its case, or a smoke id is unknown."""
    index = _yaml(root / "index.yaml", "index")
    if unknown := set(index) - INDEX_KEYS:
        raise CaseError(f"index: unknown keys {sorted(unknown)}")
    entries = index.get("cases")
    if not isinstance(entries, list) or not entries:
        raise CaseError("index: cases is a non-empty list of {case, twin, category}")
    loaded: list[tuple[HostileCase, Path, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or {"case", "twin", "category"} - set(entry):
            raise CaseError(f"index: {entry!r} is not {{case, twin, category}}")
        case = _model(HostileCase, root / str(entry["case"]), "case")
        if case.technique != entry["category"]:
            raise CaseError(f"case {case.id}: technique is not the index's category")
        loaded.append((case, root / str(entry["twin"]), str(entry["case"])))

    seen: dict[str, str] = {}
    twins_by_case: dict[str, BenignTwin] = {}
    for case, twin_path, where in loaded:
        if not twin_path.is_file():
            raise CaseError(f"case {case.id}: its twin file {twin_path.name} is missing")
        twin = _model(BenignTwin, twin_path, "twin")
        for item_id, item_where in ((case.id, where), (twin.id, twin_path.name)):
            if item_id in seen:
                raise CaseError(f"id {item_id!r} is in both {seen[item_id]} and {item_where}")
            seen[item_id] = item_where
        twins_by_case[case.id] = twin
    for case, _, _ in loaded:
        _check_case(case, root)
        _check_twin(case, twins_by_case[case.id])

    cases = {case.id: case for case, _, _ in loaded}
    smoke = tuple(str(s) for s in index.get("smoke") or [])
    if unknown_smoke := [s for s in smoke if s not in cases]:
        raise CaseError(f"index: smoke names unknown cases {unknown_smoke}")
    meta = index.get("meta") or {}
    if not isinstance(meta, dict):
        raise CaseError("index: meta must be a mapping")
    return HostileSet(
        version=int(index.get("version", 1)),
        cases=cases,
        twins={twin.id: twin for twin in twins_by_case.values()},
        smoke=smoke,
        meta=meta,
    )


@dataclass(frozen=True)
class SkillRef:
    profile: str
    skill: str


def discover_skills(profiles: Path = REPO / "profiles") -> list[SkillRef]:
    raise NotImplementedError


def coverage_gaps(hostile: HostileSet, skills: Sequence[SkillRef]) -> list[str]:
    raise NotImplementedError
