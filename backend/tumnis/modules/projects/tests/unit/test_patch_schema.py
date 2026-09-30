"""The project patch contract says what the server accepts (P1-02): `local_decisions_only`
may be left out but never set to null, which `update_project` refuses with 422."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-2.7")
@pytest.mark.wp("P1-02")
def test_local_decisions_only_is_optional_but_not_nullable_in_the_schema() -> None:
    from tumnis.modules.projects.api import ProjectPatch  # noqa: PLC0415

    schema = ProjectPatch.model_json_schema()
    assert "local_decisions_only" not in schema.get("required", [])
    field = schema["properties"]["local_decisions_only"]
    assert field["type"] == "boolean"
    assert "anyOf" not in field
