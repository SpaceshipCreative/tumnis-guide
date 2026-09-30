"""Conflict copies get a readable, free name (P1-15, FR-15.12): `name (conflict YYYY-MM-DD).ext`,
then a number when that is taken too."""

from __future__ import annotations

from datetime import date

import pytest

DAY = date(2026, 3, 9)
MARK = "(conflict 2026-03-09)"

CASES = [
    ("notes/plan.md", set(), f"notes/plan {MARK}.md"),
    ("Makefile", set(), f"Makefile {MARK}"),
    (".env", set(), f".env {MARK}"),
    ("config/.env", set(), f"config/.env {MARK}"),
    ("a.tar.gz", set(), f"a.tar {MARK}.gz"),
    ("notes/plan.md", {f"notes/plan {MARK}.md"}, f"notes/plan {MARK} 2.md"),
    (
        "notes/plan.md",
        {f"notes/plan {MARK}.md", f"notes/plan {MARK} 2.md"},
        f"notes/plan {MARK} 3.md",
    ),
    ("notes/plan.md", {"notes/PLAN (CONFLICT 2026-03-09).md"}, f"notes/plan {MARK} 2.md"),
]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
@pytest.mark.parametrize(
    ("path", "siblings", "expected"),
    CASES,
    ids=["plan.md", "Makefile", ".env", "nested-dotfile", "a.tar.gz", "taken-2", "taken-3", "case"],
)
def test_conflict_names(path: str, siblings: set[str], expected: str) -> None:
    """T-P1-15-04
    `plan.md` -> `plan (conflict 2026-03-09).md`; the extension is the last suffix
    (`a.tar.gz`), and a name without one (`Makefile`) or a dotfile (`.env`) gets the marker
    at the end. A taken conflict name (compared ignoring case) gets ` 2`, then ` 3`.
    """
    from tumnis.modules.knowledge import sync_rules  # noqa: PLC0415

    assert sync_rules.conflict_name(path, DAY, frozenset(siblings)) == expected
