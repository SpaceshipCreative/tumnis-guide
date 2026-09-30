"""The hostile suite's pass rule (P2-11, SAF-6). Name stubs until the TDD steps fill them
in."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.cases import SchemaName
from harness.hostile import HostileCase


@dataclass(frozen=True)
class RecordedCall:
    server: str
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    result: Any = None


@dataclass(frozen=True)
class Transcript:
    calls: tuple[RecordedCall, ...]
    output: dict[str, Any] | None
    output_schema: SchemaName | None = None


@dataclass(frozen=True)
class Verdict:
    passed: bool
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunResult:
    label: str
    verdicts: tuple[Verdict, ...]


def load_transcript(path: Path) -> Transcript:
    raise NotImplementedError


def judge(case: HostileCase, transcript: Transcript) -> Verdict:
    raise NotImplementedError


def judge_twin(case: HostileCase, transcript: Transcript) -> Verdict:
    raise NotImplementedError


def case_verdict(verdicts: Sequence[Verdict]) -> Verdict:
    raise NotImplementedError


def suite_verdict(results: Sequence[RunResult]) -> Verdict:
    raise NotImplementedError
