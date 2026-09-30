"""Hostile coverage (P2-11, SAF-6): every skill in the profiles meets hostile content from
every applicable source, so a new skill without cases, or without a way to inject them,
fails the build."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.req("SAF-6")
@pytest.mark.wp("P2-11")
def test_every_skill_has_hostile_coverage(tmp_path: Path) -> None:
    """T-P2-11-04
    The skills found are exactly the directories under profiles/*/skills, and the
    committed hostile set covers each with at least one case per applicable source. A set
    holding only email cases leaves chat, note and document uncovered for every skill; a
    new skill the harness cannot inject into is a gap as well.
    """
    from harness import REPO
    from harness.hostile import HOSTILE_ROOT, coverage_gaps, discover_skills, load_hostile

    profiles = REPO / "profiles"
    skills = discover_skills(profiles)
    found = {(skill.profile, skill.skill) for skill in skills}
    on_disk = {(p.parent.parent.name, p.name) for p in profiles.glob("*/skills/*") if p.is_dir()}
    assert found == on_disk
    assert {("project-template", "enrich"), ("master", "plan")} <= found

    hostile = load_hostile(HOSTILE_ROOT)
    assert coverage_gaps(hostile, skills) == []

    email_only = hostile.only(lambda case: case.source == "email")
    gaps = coverage_gaps(email_only, skills)
    for skill in skills:
        for source in ("chat", "note", "document"):
            assert any(skill.skill in gap and source in gap for gap in gaps), (skill, source)

    new_skill = tmp_path / "profiles" / "project-template" / "skills" / "orchestrate"
    new_skill.mkdir(parents=True)
    (new_skill / "SKILL.md").write_text("---\nname: orchestrate\n---\n", encoding="utf-8")
    extra = discover_skills(tmp_path / "profiles")
    assert [(s.profile, s.skill) for s in extra] == [("project-template", "orchestrate")]
    assert any("orchestrate" in gap for gap in coverage_gaps(hostile, [*skills, *extra]))
