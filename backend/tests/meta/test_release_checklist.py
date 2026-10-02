"""The v1.0.0 release checklist, docs/RELEASE-CHECKLIST.md (P4-06, REL-4, SEC-7).

Thirteen numbered rows, each a check and its evidence. Off a release branch a row's
evidence may still read "pending"; on a release branch (`release/*`) or a tag build every
row must link its evidence, so the release cannot be cut with a row unproven.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CHECKLIST = REPO / "docs" / "RELEASE-CHECKLIST.md"
ROWS = 13
_LINK = re.compile(r"\[[^\]]+\]\((?:https?://|\.{0,2}/|[\w.-]+/)[^)\s]+\)|https?://\S+")
_ROW = re.compile(r"^\|\s*(\d+)\s*\|(.*)\|(.*)\|\s*$")


def checklist_rows(markdown: str) -> list[tuple[int, str, str]]:
    """(number, check, evidence) for every numbered row of the checklist table."""
    rows = []
    for line in markdown.splitlines():
        match = _ROW.match(line)
        if match:
            rows.append((int(match.group(1)), match.group(2).strip(), match.group(3).strip()))
    return rows


def evidence_problems(rows: list[tuple[int, str, str]], *, release: bool) -> list[str]:
    """Rows whose evidence is not good enough: on a release, every row needs a link and
    none may be pending; before it, a row is a link or says "pending"."""
    problems = []
    for number, _check, evidence in rows:
        pending = evidence.lower().startswith("pending")
        linked = bool(_LINK.search(evidence))
        if release and (pending or not linked):
            problems.append(f"row {number}: needs an evidence link on a release ({evidence!r})")
        elif not release and not (pending or linked):
            problems.append(f"row {number}: neither a link nor pending ({evidence!r})")
    return problems


def on_release(env: Mapping[str, str]) -> bool:
    """A tag build, or a `release/*` branch (the PR's head branch, else the pushed ref)."""
    if env.get("GITHUB_REF_TYPE") == "tag":
        return True
    branch = env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME") or ""
    return branch.startswith("release/")


@pytest.mark.req("REL-4", "SEC-7")
@pytest.mark.wp("P4-06")
def test_checklist_items_have_evidence_links() -> None:
    """T-P4-06-06
    RELEASE-CHECKLIST.md has rows 1 to 13, each with a check; on a release branch or tag
    every row links its evidence and none is pending (elsewhere a row may be pending);
    and the release gate itself refuses a pending row.
    """
    rows = checklist_rows(CHECKLIST.read_text())
    assert [number for number, _, _ in rows] == list(range(1, ROWS + 1))
    assert all(check for _, check, _ in rows)
    assert evidence_problems(rows, release=on_release(os.environ)) == []

    assert evidence_problems([(13, "Tag v1.0.0", "pending")], release=True)
    assert evidence_problems([(1, "A0 to A4 green", "see CI")], release=False)
    assert on_release({"GITHUB_HEAD_REF": "release/1.0.0"})
    assert on_release({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v1.0.0"})
    assert not on_release({"GITHUB_HEAD_REF": "wp/P4-06", "GITHUB_REF_NAME": "151/merge"})
