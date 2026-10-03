"""APP-F05 (final application test, was APP-10; Scott decision 98): the published OpenAPI
document says `GET /v1/plan/{day}` answers 200 with the day's plan or `null`, so the
generated client accepts "no plan yet" as an answer instead of a 404 the browser logs."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.contract, pytest.mark.req("FR-4.3", "FR-1.2"), pytest.mark.wp("P1-11")]


def test_plan_read_declares_a_nullable_plan(repo_root: Path) -> None:
    """APP-F05: the 200 answer of `planning_get_plan` is a `PlanOut` or `null`."""
    spec = json.loads((repo_root / "schemas/openapi.json").read_text())
    read = spec["paths"]["/v1/plan/{day}"]["get"]
    assert read["operationId"] == "planning_get_plan"
    schema = read["responses"]["200"]["content"]["application/json"]["schema"]
    assert {"$ref": "#/components/schemas/PlanOut"} in schema["anyOf"]
    assert {"type": "null"} in schema["anyOf"]
