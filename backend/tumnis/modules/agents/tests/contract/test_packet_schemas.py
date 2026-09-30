"""Enrichment and planning skill contracts against their committed JSON Schemas (P1-05,
FR-14.7, R-02).

The four bodies a TaskPacket carries for phase 1 skills are `@versioned` models in
`agents/skill_io.py`; `make gen` writes their schemas under `schemas/enrichment/v1/` and
`schemas/planning/v1/`, and their golden examples are the schema fixtures
(`backend/tests/contract/fixtures/<family>/<name>/v1.json`).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis.core.schemas import Family

pytestmark = [pytest.mark.contract]

SKILL_SCHEMAS: tuple[tuple[Family, str], ...] = (
    ("enrichment", "request"),
    ("enrichment", "result"),
    ("planning", "request"),
    ("planning", "result"),
)


@pytest.mark.req("FR-14.7")
@pytest.mark.wp("P1-05")
@pytest.mark.xfail(strict=True, reason="spec:P1-05")
@pytest.mark.parametrize(("family", "name"), SKILL_SCHEMAS)
def test_schemas_generated_and_examples_valid(family: Family, name: str, repo_root: Path) -> None:
    """T-P1-05-11
    The committed schemas/<family>/v1/<name>.json equals what the model generates, and
    the golden example validates against it and parses into the model.
    """
    from tumnis.core.schemas import (  # noqa: PLC0415
        dump_json,
        json_schema,
        parse_versioned,
        registry,
    )
    from tumnis.modules.agents import skill_io  # noqa: F401, PLC0415  # registers the models

    spec = registry().versions(family, name)[1]
    committed = (repo_root / spec.path).read_text()
    assert committed == dump_json(json_schema(spec))

    example = json.loads(
        (repo_root / "backend/tests/contract/fixtures" / family / name / "v1.json").read_text()
    )
    Draft202012Validator(json.loads(committed)).validate(example)
    parsed = parse_versioned(family, name, example)
    assert type(parsed) is spec.model
    assert json.loads(parsed.model_dump_json()) == example
