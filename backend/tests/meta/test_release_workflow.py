"""Release workflow (P0-30, REL-4, SAAS-1, SEC-7): a `v*.*.*` tag is rehearsed (deploy N,
N+1, N), then released with its changelog notes, a CycloneDX SBOM and the OpenAPI spec;
the same rehearsal runs on every PR that touches a migration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
RELEASE_YML = WORKFLOWS / "release.yml"
VERSION_SKEW_YML = WORKFLOWS / "version-skew.yml"
REHEARSAL = "scripts/release/rollback_rehearsal.sh"
SBOM_SH = REPO / "scripts" / "ci" / "sbom.sh"


def _workflow(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), path
    # YAML 1.1 reads the bare key `on` as the boolean true.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = job.get("steps", [])
    return steps


def _index(steps: list[dict[str, Any]], predicate: Any, what: str) -> int:
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    pytest.fail(f"no step {what}")


def _run(step: dict[str, Any]) -> str:
    return str(step.get("run", ""))


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _is_cyclonedx_sbom(step: dict[str, Any]) -> bool:
    uses, with_ = str(step.get("uses", "")), step.get("with", {}) or {}
    if uses.startswith("aquasecurity/trivy-action"):
        return with_.get("format") == "cyclonedx" and with_.get("output") == "sbom.cdx.json"
    run = _run(step)
    return "scripts/ci/sbom.sh" in run and "sbom.cdx.json" in run


@pytest.mark.contract
@pytest.mark.req("SAAS-1", "SEC-7")
@pytest.mark.wp("P0-30")
def test_release_attaches_sbom_and_openapi() -> None:
    """T-P0-30-05
    release.yml runs on `v*.*.*` tags; the release job needs the rehearsal, checks the
    changelog, pushes the image built with VERSION, writes a CycloneDX SBOM and copies
    schemas/openapi.json, then `gh release create --verify-tag` attaches both files with
    the changelog section as notes.
    """
    wf = _workflow(RELEASE_YML)
    assert wf["on"]["push"]["tags"] == ["v*.*.*"]
    assert wf["permissions"]["contents"] == "write"
    assert wf["permissions"]["packages"] == "write"

    release = wf["jobs"]["release"]
    assert "rehearsal" in _needs(release)
    steps = _steps(release)

    changelog = _index(
        steps,
        lambda s: "scripts/release/check_changelog.py" in _run(s) and "--extract" not in _run(s),
        "checking the changelog",
    )
    build = _index(
        steps,
        lambda s: (
            str(s.get("uses", "")).startswith("docker/build-push-action")
            and "VERSION=" in str((s.get("with") or {}).get("build-args", ""))
            and (s.get("with") or {}).get("push") is True
        ),
        "building and pushing the image with the VERSION build arg",
    )
    sbom = _index(steps, _is_cyclonedx_sbom, "writing a CycloneDX SBOM to sbom.cdx.json")
    openapi = _index(
        steps,
        lambda s: (
            "schemas/openapi.json" in _run(s) and "openapi-${GITHUB_REF_NAME}.json" in _run(s)
        ),
        "copying schemas/openapi.json to openapi-<tag>.json",
    )
    notes = _index(
        steps,
        lambda s: "check_changelog.py --extract" in _run(s) and "notes.md" in _run(s),
        "extracting the release notes",
    )
    create = _index(steps, lambda s: "gh release create" in _run(s), "creating the release")

    command = _run(steps[create])
    for part in ("sbom.cdx.json", "openapi-${GITHUB_REF_NAME}.json", "--notes-file notes.md"):
        assert part in command, part
    assert "--verify-tag" in command
    assert changelog < build < sbom < create
    assert openapi < create
    assert notes < create
    if "scripts/ci/sbom.sh" in _run(steps[sbom]):
        assert "--format cyclonedx" in SBOM_SH.read_text()


@pytest.mark.contract
@pytest.mark.req("REL-4")
@pytest.mark.wp("P0-30")
@pytest.mark.xfail(strict=True, reason="spec:P0-30")
def test_rehearsal_runs_before_release_and_on_migration_prs() -> None:
    """T-P0-30-01
    The rollback rehearsal (deploy N, N+1, N) is wired twice: release.yml's rehearsal job
    runs it from the previous tag to the tagged commit, and the version-skew job runs it on
    pull requests from the latest main image to the PR's commit when the PR touches
    `**/migrations/**` (and exits green otherwise, so it can be a required check).
    """
    script = REPO / REHEARSAL
    assert script.is_file()
    assert os.access(script, os.X_OK), f"{REHEARSAL} is not executable"

    release = _workflow(RELEASE_YML)["jobs"]["rehearsal"]
    step = _steps(release)[
        _index(_steps(release), lambda s: REHEARSAL in _run(s), "running the rehearsal")
    ]
    assert "git describe --tags" in _run(step)  # N: the tag before this one
    assert "GITHUB_SHA" in _run(step)  # N+1: the tagged commit

    skew = _workflow(VERSION_SKEW_YML)
    assert "pull_request" in skew["on"]
    assert "paths" not in (skew["on"]["pull_request"] or {}), "a filtered check never reports"
    job = skew["jobs"]["version-skew"]
    steps = _steps(job)
    rehearse = _index(steps, lambda s: REHEARSAL in _run(s), "running the rehearsal")
    guard = _index(
        steps, lambda s: "migrations/" in _run(s), "detecting a change under **/migrations/**"
    )
    assert guard < rehearse
    assert "if" in steps[rehearse], "the rehearsal runs only when a migration changed"
    assert ":main" in _run(steps[rehearse])  # N: the latest main image
