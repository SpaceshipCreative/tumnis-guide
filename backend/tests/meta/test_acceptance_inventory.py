"""Acceptance inventory: every acceptance ID of a started phase has a test (P0-05).

Tests are found with the traceability job's extractors (pytest collection through the
pytest_trace plugin, Playwright and Vitest blocks through ts_tests.mjs), so the check sees
exactly what traceability sees. A test carries an acceptance ID as a tag (`@A0.1` in
Playwright, `req("A0.3")` in pytest), as its title prefix (`A0.1 sign in ...`) or, in
pytest, through its file name (`test_a0_3_tenant_isolation.py` is A0.3).

A phase's acceptance suite is committed red on the phase's first day, when the phase
turns `active` in docs/plan/work-packages.yaml; planned phases are not checked yet, and
each is covered automatically once it starts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

from tests.ci._scripts import REPO, load, node_with_typescript

PLAN = REPO / "docs" / "plan" / "work-packages.yaml"
STARTED = frozenset({"active", "done"})
PYTEST_FILE_ID = re.compile(r"(?:^|/)test_a(\d+)_(\d+)_")


def started_acceptance_ids(plan: Path) -> dict[str, int]:
    """Acceptance ID -> phase, for every phase that is active or done."""
    data = yaml.safe_load(plan.read_text())
    return {
        str(item["id"]): int(phase["id"])
        for phase in data["phases"]
        if phase.get("status") in STARTED
        for item in phase.get("acceptance", [])
    }


def acceptance_ids_of(ref_id: str, reqs: set[str]) -> set[str]:
    """Acceptance IDs one test reference carries: tags, title prefix, pytest file name."""
    title = ref_id.rsplit(" > ", 1)[-1]
    ids = {tag for tag in reqs if re.fullmatch(r"A\d+\.\d+", tag)}
    if prefix := re.match(r"(A\d+\.\d+) ", title):
        ids.add(prefix.group(1))
    if match := PYTEST_FILE_ID.search(ref_id.split("::", 1)[0]):
        ids.add(f"A{match.group(1)}.{match.group(2)}")
    return ids


@pytest.mark.req("Quality rule 4")
@pytest.mark.wp("P0-05")
def test_every_phase_acceptance_id_has_a_tagged_test() -> None:
    """T-P0-05-07
    For each started phase in work-packages.yaml, every `acceptance[].id` appears as a
    tag or title prefix on at least one test (expected failures count: the suite is
    committed red).
    """
    if not node_with_typescript():
        assert not os.environ.get("CI"), "CI must provide node and frontend/node_modules"
        pytest.skip("node or frontend/node_modules/typescript missing; run npm ci in frontend/")
    traceability = load("traceability")
    refs = traceability.pytest_refs(REPO, None) + traceability.frontend_refs(REPO)
    found = {aid for ref in refs for aid in acceptance_ids_of(ref.id, ref.reqs)}

    expected = started_acceptance_ids(PLAN)
    assert expected, "no started phase lists acceptance IDs"
    missing = sorted(set(expected) - found)
    assert not missing, f"acceptance IDs without a test: {missing}"
