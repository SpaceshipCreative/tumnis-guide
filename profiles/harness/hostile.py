"""The hostile content suite (P2-11, SAF-6): cases of outside text that tries to steer an
agent, each with a benign twin, run against every skill. Name stubs until the TDD steps
fill them in."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness import REPO

HOSTILE_ROOT: Final = REPO / "backend" / "fixtures" / "hostile"
SOURCES: Final = ("email", "chat", "note", "document")
INJECT_POINTS: Final = ("context_item", "passage", "task_title", "digest_entry")

Source = Literal["email", "chat", "note", "document"]
InjectPoint = Literal["context_item", "passage", "task_title", "digest_entry"]


class CallPattern(BaseModel):
    """A tool call to look for: server and tool name globs, and a regular expression per
    argument name (`*`: any argument)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: str
    tool: str
    args_match: dict[str, str] = Field(default_factory=dict)


class Companion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Source
    inject_as: InjectPoint
    envelope: dict[str, str] = Field(default_factory=dict)
    content: str


class HostileCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    version: int = Field(ge=1)
    source: Source
    technique: str
    inject_as: InjectPoint
    envelope: dict[str, str] = Field(default_factory=dict)
    content: str
    companions: tuple[Companion, ...] = ()
    pdf: str | None = None
    attack_goal: str
    forbidden_calls: tuple[CallPattern, ...] = ()
    forbidden_action_classes: tuple[str, ...] = ()
    benign_twin: str
    expected_calls_on_twin: tuple[CallPattern, ...] = ()
    skills: Literal["all"] | tuple[str, ...] = "all"
    path: Path | None = None


class BenignTwin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    version: int = Field(ge=1)
    twin_of: str
    source: Source
    inject_as: InjectPoint
    envelope: dict[str, str] = Field(default_factory=dict)
    content: str
    companions: tuple[Companion, ...] = ()
    path: Path | None = None


@dataclass(frozen=True)
class HostileSet:
    version: int
    cases: dict[str, HostileCase]
    twins: dict[str, BenignTwin]
    smoke: tuple[str, ...] = ()

    def only(self, keep: Callable[[HostileCase], bool]) -> "HostileSet":
        raise NotImplementedError


@dataclass(frozen=True)
class SkillRef:
    profile: str
    skill: str


def load_hostile(root: Path = HOSTILE_ROOT) -> HostileSet:
    raise NotImplementedError


def discover_skills(profiles: Path = REPO / "profiles") -> list[SkillRef]:
    raise NotImplementedError


def coverage_gaps(hostile: HostileSet, skills: Sequence[SkillRef]) -> list[str]:
    raise NotImplementedError
