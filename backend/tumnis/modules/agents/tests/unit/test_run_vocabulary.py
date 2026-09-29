"""The run vocabulary (P1-04, R-22, FR-14.6): the enums and the `runs` CHECK constraints
hold the same values, all of them from the first revision."""

import ast
import re
from pathlib import Path

import pytest

from tumnis.modules.agents.api import RunKind, RunStatus

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

R22_KINDS = {"enrich", "plan", "task", "proposal", "stuck", "notify"}
R22_STATUSES = {
    "queued",
    "running",
    "waiting_on_human",
    "held",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "runner_lost",
}


def _check_values(name: str) -> set[str]:
    """The quoted values of the CHECK constraint `name` in the agents revisions."""
    for path in sorted(MIGRATIONS.glob("[0-9]*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (
                isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "CheckConstraint"
            ):
                continue
            keywords = {k.arg: k.value for k in node.keywords}
            named = keywords.get("name")
            if isinstance(named, ast.Constant) and named.value == name:
                sql = node.args[0]
                assert isinstance(sql, ast.Constant), name
                return set(re.findall(r"'([a-z_]+)'", str(sql.value)))
    raise AssertionError(f"no CheckConstraint named {name} in {MIGRATIONS}")


@pytest.mark.req("FR-14.6")
@pytest.mark.wp("P1-04")
def test_run_enums_match_table_checks() -> None:
    """T-P1-04-21
    RunKind and RunStatus hold exactly the R-22 values, and the `runs` table's
    ck_runs_kind and ck_runs_status constraints allow exactly those values.
    """
    assert {kind.value for kind in RunKind} == R22_KINDS
    assert {status.value for status in RunStatus} == R22_STATUSES
    assert _check_values("ck_runs_kind") == R22_KINDS
    assert _check_values("ck_runs_status") == R22_STATUSES
