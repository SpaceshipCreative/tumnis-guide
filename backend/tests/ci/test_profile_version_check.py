"""profile_version_check: a change to a Hermes profile needs a VERSION bump (P1-05, FR-5.10).

Installed profiles are updated by version (`hermes profile update`), so a change under
profiles/<name>/ that leaves profiles/<name>/VERSION as it was would never reach them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.ci._gitrepo import Repo, commit, make_repo
from tests.ci._scripts import load

if TYPE_CHECKING:
    from pathlib import Path

BASE = {
    "profiles/master/VERSION": "1.0.0\n",
    "profiles/master/distribution.yaml": "name: tumnis-master\nversion: 1.0.0\n",
    "profiles/master/SOUL.md": "You are the Tumnis master agent.\n",
    "profiles/master/skills/plan/SKILL.md": "---\nname: plan\n---\nPlan the day.\n",
    "profiles/project-template/VERSION": "1.0.0\n",
    "profiles/project-template/skills/enrich/SKILL.md": "---\nname: enrich\n---\nEnrich.\n",
    "profiles/harness/run.py": "RUNS = 3\n",
    "README.md": "Tumnis\n",
}


def _check(repo: Repo, head: str) -> int:
    script = load("profile_version_check")
    code: int = script.main(["--base", repo.base, "--head", head, "--repo", str(repo.path)])
    return code


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-05")
def test_change_without_bump_fails(tmp_path: Path) -> None:
    """T-P1-05-13
    A diff touching profiles/master/skills/ without a VERSION change fails; bumping
    VERSION (with the manifest) passes, and changes outside a profile need no bump.
    """
    repo = make_repo(tmp_path, BASE)
    skill = "profiles/master/skills/plan/SKILL.md"

    unbumped = commit(repo, {skill: "---\nname: plan\n---\nPlan the day, at most 5.\n"})
    assert _check(repo, unbumped) == 1

    bumped = commit(
        repo,
        {
            "profiles/master/VERSION": "1.0.1\n",
            "profiles/master/distribution.yaml": "name: tumnis-master\nversion: 1.0.1\n",
        },
    )
    assert _check(repo, bumped) == 0

    elsewhere = commit(repo, {"profiles/harness/run.py": "RUNS = 3  # fixed\n", "README.md": "x\n"})
    assert _check(repo, elsewhere) == 0

    template = commit(
        repo, {"profiles/project-template/skills/enrich/SKILL.md": "---\nname: enrich\n---\n"}
    )
    assert _check(repo, template) == 1
