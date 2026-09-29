"""scripts/release/check_changelog.py: a release tag is `v` + SemVer and CHANGELOG.md has
its Keep a Changelog section (P0-30, REL-4).

Collected from backend/ (pyproject testpaths lists ../scripts/release).
"""

# ruff: noqa: S101

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

HERE = Path(__file__).resolve().parent

CHANGELOG = """\
# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Something not released yet.

## [0.1.0] - 2026-10-02

### Added

- Phase 0: the walking skeleton.

### Fixed

- A bug.

## [0.0.1] - 2026-09-01

### Added

- First tag.

[Unreleased]: https://example.invalid/compare/v0.1.0...HEAD
[0.1.0]: https://example.invalid/compare/v0.0.1...v0.1.0
"""


def _load() -> ModuleType:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return importlib.import_module("check_changelog")


@pytest.mark.req("REL-4")
@pytest.mark.wp("P0-30")
@pytest.mark.xfail(strict=True, reason="spec:P0-30")
def test_tag_needs_semver_and_changelog_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T-P0-30-04
    `v0.1.0` passes with a `## [0.1.0] - YYYY-MM-DD` section; `v0.1`, `0.1.0` and a tag
    without a section fail; `--extract` prints only that section's body.
    """
    check_changelog = _load()
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG)

    def run(*args: str) -> int:
        code: int = check_changelog.main([*args, "--changelog", str(path)])
        return code

    assert run("v0.1.0") == 0
    assert run("v0.0.1") == 0
    assert run("v0.1") == 1
    assert run("0.1.0") == 1
    assert run("v0.2.0") == 1  # no section
    assert run("v01.0.0") == 1  # leading zero: not SemVer
    capsys.readouterr()

    assert run("--extract", "v0.1.0") == 0
    notes = capsys.readouterr().out
    assert "Phase 0: the walking skeleton." in notes
    assert "### Fixed" in notes
    assert "## [0.1.0]" not in notes
    assert "First tag." not in notes
    assert "not released yet" not in notes
    assert "example.invalid" not in notes
    assert run("--extract", "v0.2.0") == 1

    undated = CHANGELOG.replace("## [0.1.0] - 2026-10-02", "## [0.1.0]")
    path.write_text(undated)
    assert run("v0.1.0") == 1  # a released section carries its date
