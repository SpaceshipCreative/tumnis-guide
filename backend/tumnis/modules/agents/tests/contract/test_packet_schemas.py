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


RECORDED_BODIES: dict[str, tuple[Family, str]] = {
    "enrich": ("enrichment", "request"),
    "plan": ("planning", "request"),
}


@pytest.mark.req("FR-5.2")
@pytest.mark.wp("P1-05")
def test_recorded_packets_match_the_models(repo_root: Path) -> None:
    """The skill harness's recorded packets (profiles/tests/recordings/<skill>/) are
    TaskPackets whose body validates against the committed request schema and whose
    prompt_text is what render_prompt makes of that body, so a schema change fails here
    until they are regenerated."""
    from tumnis.core.schemas import parse_versioned  # noqa: PLC0415
    from tumnis.modules.agents.packet_builder import TaskPacket, render_prompt  # noqa: PLC0415

    recordings = sorted((repo_root / "profiles/tests/recordings").glob("*/*.packet.json"))
    assert {p.parent.name for p in recordings} == set(RECORDED_BODIES)
    for path in recordings:
        packet = TaskPacket.model_validate_json(path.read_text())
        family, name = RECORDED_BODIES[path.parent.name]
        assert packet.skill == path.parent.name
        assert (packet.output_schema.family, packet.output_schema.name) == (family, "result")
        schema = json.loads((repo_root / f"schemas/{family}/v1/{name}.json").read_text())
        Draft202012Validator(schema).validate(packet.body)
        body = parse_versioned(family, name, packet.body)
        assert json.loads(body.model_dump_json()) == packet.body
        assert packet.prompt_text == render_prompt(packet.skill, packet.output_schema, packet.body)
