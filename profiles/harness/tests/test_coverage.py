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


# The skills each profile ships once P2-12 lands (P1-05's enrich and plan included).
SKILLS = {
    ("project-template", "enrich"),
    ("project-template", "orchestrate"),
    ("project-template", "coding"),
    ("project-template", "gated-actions"),
    ("project-template", "project-digest"),
    ("master", "plan"),
    ("master", "orchestrate-master"),
    ("master", "workspace-digest"),
}
# The eight gated action classes the plan's fixture table names, in the project policy's
# vocabulary (projects.rules GATED_DEFAULT; the plan's "proxmox_destructive" row is
# proxmox_delete_guest there).
GATED_CLASSES = (
    "send_email",
    "merge_main",
    "push_main",
    "force_push",
    "deploy_production",
    "proxmox_delete_guest",
    "spend_money",
    "delete_files",
)


@pytest.mark.req("FR-5.3")
@pytest.mark.wp("P2-12")
def test_every_skill_has_cases(capsys: pytest.CaptureFixture[str]) -> None:
    """T-P2-12-11
    The profiles ship exactly the template and master skills; every skill directory has at
    least one case under tests/cases/<skill>/ for its own profile, and hostile coverage
    (P2-11) with no gaps. gated-actions has an approved and a denied case for each of the
    eight gated classes. `python -m harness coverage` reports no gaps and exits 0.
    """
    import harness.__main__ as cli
    from harness import REPO
    from harness.cases import load_cases
    from harness.hostile import coverage_gaps, discover_skills, load_hostile

    skills = discover_skills(REPO / "profiles")
    assert {(s.profile, s.skill) for s in skills} == SKILLS

    cases = load_cases(REPO / "profiles" / "tests" / "cases")
    for skill in skills:
        own = [c for c in cases if (c.profile, c.skill) == (skill.profile, skill.skill)]
        assert own, f"{skill.profile}/{skill.skill} has no case"
        assert all(c.path.parent.name == skill.skill for c in own)
    assert coverage_gaps(load_hostile(), skills) == []

    gated = {c.path.stem for c in cases if c.skill == "gated-actions"}
    for klass in GATED_CLASSES:
        assert {f"{klass}_approved", f"{klass}_denied"} <= gated, klass

    assert cli.main(["coverage"]) == 0
    assert "0 gaps" in capsys.readouterr().out
