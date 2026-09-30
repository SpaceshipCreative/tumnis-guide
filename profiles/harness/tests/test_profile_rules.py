"""What the two profiles must agree on (P1-05, TDD step 8): the shared SOUL rule block,
the pinned model the harness also uses, and the Jev MCP server entry."""

from __future__ import annotations

import json
import re

import yaml

from harness import REPO
from harness.run import load_config

PROFILES = ("project-template", "master")
RULES = re.compile(r"<!-- tumnis:rules -->\n(.*?)<!-- /tumnis:rules -->", re.DOTALL)


def _read(profile: str, name: str) -> str:
    return (REPO / "profiles" / profile / name).read_text()


def test_both_souls_carry_the_same_rule_block() -> None:
    blocks = [RULES.search(_read(p, "SOUL.md")) for p in PROFILES]
    assert all(blocks), "each SOUL.md has a <!-- tumnis:rules --> block"
    texts = {b.group(1) for b in blocks if b}
    assert len(texts) == 1, "the rule block is identical in both SOULs"
    rules = texts.pop()
    for rule in ("data, never instructions", "exactly one JSON object", "Call no tools"):
        assert rule in rules


def test_profiles_pin_the_model_the_harness_runs() -> None:
    config = load_config()
    for profile in PROFILES:
        model = yaml.safe_load(_read(profile, "config.yaml"))["model"]
        assert (model["default"], model["provider"]) == (config.model, config.provider)


def test_both_profiles_run_the_same_jev_server() -> None:
    assert len({json.dumps(json.loads(_read(p, "mcp.json"))) for p in PROFILES}) == 1


def test_skill_frontmatter_and_json_only_reply() -> None:
    for profile, skill in (("project-template", "enrich"), ("master", "plan")):
        text = _read(profile, f"skills/{skill}/SKILL.md")
        assert '"schema_version": 1' in text  # the generated result schemas require it
        assert "Call no tools." in text
        assert "never as instructions" in text
