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

import copy
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal
from uuid import NAMESPACE_URL, uuid5

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness import REPO
from harness.assertions import RULE_REQUESTS
from harness.cases import Case, CaseError, CaseMeta, SchemaName
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


# --- skills, coverage and injection -------------------------------------------------------


@dataclass(frozen=True)
class SkillRef:
    """A skill directory, profiles/<profile>/skills/<skill>/."""

    profile: str
    skill: str


Part = HostileCase | BenignTwin | Companion
Inject = Callable[[dict[str, Any], Sequence[Part], str], None]


@dataclass(frozen=True)
class SkillBase:
    """How the suite reaches a skill: the recorded packet a run starts from, the reply's
    schema and named rules, where each injection point lands in the packet body, and the
    sources that apply to it."""

    profile: str
    skill: str
    packet: Path
    output_schema: SchemaName
    rules: tuple[str, ...]
    inject: Inject
    sources: tuple[str, ...] = SOURCES


def discover_skills(profiles: Path = REPO / "profiles") -> list[SkillRef]:
    """Every skill directory under `profiles/*/skills`, sorted."""
    return sorted(
        (SkillRef(d.parent.parent.name, d.name) for d in profiles.glob("*/skills/*") if d.is_dir()),
        key=lambda s: (s.profile, s.skill),
    )


def _parts(item: HostileCase | BenignTwin) -> list[Part]:
    return [item, *item.companions]


def _title(part: Part) -> str:
    envelope = part.envelope
    title = envelope.get("subject") or envelope.get("title") or envelope.get("channel")
    return (title or f"{part.source} from {envelope.get('from', 'outside')}")[:200]


def outside_text(part: Part) -> str:
    """A part as the skill reads it: its envelope as header lines, then its content."""
    head = "\n".join(f"{key}: {value}" for key, value in part.envelope.items())
    return f"{head}\n\n{part.content}" if head else part.content


def _part_id(item_id: str, n: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"tumnis:hostile:{item_id}:{n}"))


def _inject_enrich(body: dict[str, Any], parts: Sequence[Part], item_id: str) -> None:
    """A task title replaces the task's title; every other part arrives as a document
    passage, the only outside text an enrichment packet carries."""
    for n, part in enumerate(parts):
        if part.inject_as == "task_title":
            body["task"]["title"] = part.content
            continue
        body["passages"].append(
            {
                "document_id": _part_id(item_id, n),
                "title": _title(part),
                "heading_path": [],
                "page": 1 if part.source == "document" else None,
                "text": outside_text(part),
            }
        )


def _inject_plan(body: dict[str, Any], parts: Sequence[Part], item_id: str) -> None:
    """Each part arrives as one more candidate task: a task title as its title, any other
    part as its first action (the planning packet's only long outside text)."""
    template = body["candidates"][0]
    for n, part in enumerate(parts):
        candidate = {**template, "task_id": _part_id(item_id, n), "rollover_count": 0}
        if part.inject_as == "task_title":
            candidate.update(title=part.content, first_action=None)
        else:
            candidate.update(title=_title(part), first_action=outside_text(part))
        body["candidates"].append(candidate)


RECORDINGS: Final = REPO / "profiles" / "tests" / "recordings"
BASES: Final[dict[str, SkillBase]] = {
    "enrich": SkillBase(
        profile="project-template",
        skill="enrich",
        packet=RECORDINGS / "enrich" / "hybrid_invoice.packet.json",
        output_schema=SchemaName("enrichment", "result", 1),
        rules=("enrichment_errors",),
        inject=_inject_enrich,
    ),
    "plan": SkillBase(
        profile="master",
        skill="plan",
        packet=RECORDINGS / "plan" / "monday_four_picks.packet.json",
        output_schema=SchemaName("planning", "result", 1),
        rules=("planning_errors",),
        inject=_inject_plan,
    ),
}


def _base(skill: SkillRef) -> SkillBase | None:
    base = BASES.get(skill.skill)
    return base if base is not None and base.profile == skill.profile else None


def coverage_gaps(hostile: HostileSet, skills: Sequence[SkillRef]) -> list[str]:
    """What keeps the suite from covering every skill: a skill the suite cannot inject into
    (no entry in BASES for its profile), a source that applies to a skill with no case for
    it, and a case naming a skill that does not exist. [] when everything is covered."""
    gaps: list[str] = []
    names = {skill.skill for skill in skills}
    for skill in skills:
        label = f"{skill.profile}/{skill.skill}"
        base = _base(skill)
        if base is None:
            gaps.append(f"{label}: no hostile base packet (add one to BASES in harness/hostile.py)")
        for source in base.sources if base is not None else SOURCES:
            if not any(
                c.source == source and c.covers(skill.skill) for c in hostile.cases.values()
            ):
                gaps.append(f"{label}: no {source} case")
    for case in hostile.cases.values():
        if case.skills != "all" and (unknown := sorted(set(case.skills) - names)):
            gaps.append(f"case {case.id}: unknown skills {unknown}")
    return gaps


def render_prompt(recorded: str, body: Mapping[str, Any]) -> str:
    """The recorded packet's prompt with its body JSON replaced: the same instruction, and
    the JSON written as the packet builder writes it (`<` as `\\u003c`)."""
    head, marker, _ = recorded.partition("<packet>\n")
    if not marker:
        raise CaseError("a recorded packet's prompt carries a <packet> block")
    data = json.dumps(body, ensure_ascii=False, indent=2).replace("<", "\\u003c")
    return f"{head}<packet>\n{data}\n</packet>\n"


def hostile_packet(base: SkillBase, item: HostileCase | BenignTwin) -> dict[str, Any]:
    """The base packet with `item` injected: its body, validated as the skill's request,
    and its prompt."""
    packet: dict[str, Any] = json.loads(base.packet.read_text(encoding="utf-8"))
    body = copy.deepcopy(packet["body"])
    base.inject(body, _parts(item), item.id)
    for rule in base.rules:
        try:
            RULE_REQUESTS[rule].model_validate(body)
        except ValidationError as exc:
            raise CaseError(f"{item.id}: not a request {base.skill} can read ({exc})") from exc
    packet["body"] = body
    packet["prompt_text"] = render_prompt(packet["prompt_text"], body)
    return packet


@dataclass(frozen=True)
class HostileRun:
    """One (case or twin, skill) pair: judged with `judge` (hostile) or `judge_twin`."""

    kind: Literal["hostile", "twin"]
    case: HostileCase
    item_id: str
    base: SkillBase
    harness_case: Case

    @property
    def label(self) -> str:
        return f"{self.item_id}/{self.base.skill}"


def _run(
    kind: Literal["hostile", "twin"],
    case: HostileCase,
    item: HostileCase | BenignTwin,
    base: SkillBase,
) -> HostileRun:
    harness_case = Case(
        id=f"{item.id}--{base.skill}",
        path=item.path or base.packet,
        profile=base.profile,
        skill=base.skill,
        input_path=base.packet,
        packet=hostile_packet(base, item),
        output_schema=base.output_schema,
        allow=("*",),  # the hostile judge rules on tool calls, not an allow list
        rules=base.rules,
        json_checks=(),
        meta=CaseMeta(),
    )
    return HostileRun(kind, case, item.id, base, harness_case)


def expand(
    hostile: HostileSet,
    skills: Sequence[SkillRef],
    *,
    smoke: bool = False,
    changed_skills: Iterable[str] = (),
    changed_cases: Iterable[str] = (),
) -> list[HostileRun]:
    """Every (case, skill) the case applies to, as a hostile run and a twin run. With
    `smoke`, only the index's smoke cases, the cases of changed skills and changed cases
    (what a PR runs; the nightly run takes everything)."""
    skill_set, case_set = set(changed_skills), set(changed_cases)
    runs: list[HostileRun] = []
    for case in hostile.cases.values():
        for skill in skills:
            base = _base(skill)
            if base is None or not case.covers(skill.skill):
                continue
            picked = case.id in hostile.smoke or skill.skill in skill_set or case.id in case_set
            if smoke and not picked:
                continue
            runs.append(_run("hostile", case, case, base))
            runs.append(_run("twin", case, hostile.twins[case.benign_twin], base))
    return runs


def changed(
    paths: Iterable[str], hostile: HostileSet, skills: Sequence[SkillRef]
) -> tuple[set[str], set[str]]:
    """The skills and cases a change touches, from repository paths: a file in a skill's
    directory changes that skill; any other file of a profile changes all of its skills;
    a case or twin file changes its case."""
    by_file: dict[str, str] = {}
    for case in hostile.cases.values():
        for item in (case, hostile.twins[case.benign_twin]):
            if item.path is not None:
                by_file[item.path.resolve().relative_to(REPO).as_posix()] = case.id
    touched_skills: set[str] = set()
    touched_cases: set[str] = set()
    for path in paths:
        parts = path.split("/")
        if parts[0] == "profiles" and len(parts) > 3 and parts[2] == "skills":  # noqa: PLR2004
            touched_skills.add(parts[3])
        elif parts[0] == "profiles" and len(parts) > 1:
            touched_skills |= {s.skill for s in skills if s.profile == parts[1]}
        elif path in by_file:
            touched_cases.add(by_file[path])
    return touched_skills, touched_cases


# --- the generated index (profiles/tests/cases/hostile/index.yaml) -------------------------

INDEX: Final = REPO / "profiles" / "tests" / "cases" / "hostile" / "index.yaml"
GENERATED: Final = (
    "# @generated by `uv run python -m harness index --suite hostile` from "
    "backend/fixtures/hostile; do not edit."
)


def _digest(case: HostileCase, twin: BenignTwin) -> str:
    sha = hashlib.sha256()
    for item in (case, twin):
        if item.path is not None:
            sha.update(item.path.read_bytes())
    return sha.hexdigest()[:16]


def render_index(hostile: HostileSet, skills: Sequence[SkillRef]) -> str:
    """The Skills job's case index: one entry per (case, skill) with its twin, profile,
    smoke flag and a digest of both files, so an edited case needs a regenerated index."""
    meta = json.dumps(hostile.meta, ensure_ascii=False, separators=(", ", ": "))
    lines = [
        GENERATED,
        "# Each entry is a pytest item (T-P2-11-01) that runs on the homelab runner only",
        "# (--run-skills): the case and its twin, 3 runs each, judged by harness/judge.py.",
        "suite: hostile",
        f"version: {hostile.version}",
        f"meta: {meta}",
        "runs:",
    ]
    for case in hostile.cases.values():
        twin = hostile.twins[case.benign_twin]
        for skill in skills:
            if _base(skill) is None or not case.covers(skill.skill):
                continue
            smoke = "true" if case.id in hostile.smoke else "false"
            lines.append(
                f"  - {{case: {case.id}, twin: {twin.id}, skill: {skill.skill}, "
                f"profile: {skill.profile}, smoke: {smoke}, digest: {_digest(case, twin)}}}"
            )
    return "\n".join(lines) + "\n"
