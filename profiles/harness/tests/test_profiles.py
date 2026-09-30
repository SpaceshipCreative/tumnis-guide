"""The two Hermes profile distributions in the repo (P1-05, FR-5.10, FR-11.6).

Each is a Hermes profile distribution (distribution.yaml, SOUL.md, config.yaml, mcp.json,
skills/): the repo holds the known-good baseline, and the profile on the agent host keeps
its own memories, sessions and credentials, which a distribution never carries.
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import Any

import pytest
import yaml

from harness import REPO

# profile directory -> its phase 1 skill
PROFILES = {"project-template": "enrich", "master": "plan"}
NEVER_SHIPPED = (".env", "auth.json", "memories", "sessions", "state.db", "logs")


def _env_names(manifest: dict[str, Any]) -> set[str]:
    return {str(entry["name"]) for entry in manifest.get("env_requires") or []}


def _frontmatter(text: str) -> dict[str, Any]:
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md starts with YAML frontmatter between --- lines"
    data: dict[str, Any] = yaml.safe_load(match.group(1))
    return data


@pytest.mark.req("FR-5.10", "FR-11.6")
@pytest.mark.wp("P1-05")
def test_distribution_layout() -> None:
    """T-P1-05-12
    Both profiles have a distribution.yaml whose version equals VERSION, a SOUL, a config,
    an mcp.json whose `jev` server runs profiles/shared/jev-mcp at its pinned version with
    TYPESAFE_API_KEY from the profile's .env, and the phase 1 skill; no credentials or
    user data are in the repo.
    """
    jev_mcp = tomllib.loads((REPO / "profiles/shared/jev-mcp/pyproject.toml").read_text())
    jev_version = jev_mcp["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", jev_version)

    for profile, skill in PROFILES.items():
        root = REPO / "profiles" / profile

        manifest = yaml.safe_load((root / "distribution.yaml").read_text())
        version = (root / "VERSION").read_text().strip()
        assert manifest["name"] == f"tumnis-{profile}"
        assert re.fullmatch(r"\d+\.\d+\.\d+", version)
        assert manifest["version"] == version
        assert "TYPESAFE_API_KEY" in _env_names(manifest)

        assert (root / "SOUL.md").read_text().strip()
        assert isinstance(yaml.safe_load((root / "config.yaml").read_text()), dict)

        jev = json.loads((root / "mcp.json").read_text())["mcpServers"]["jev"]
        args = " ".join(jev["args"])
        assert "#subdirectory=profiles/shared/jev-mcp" in args
        assert f"@jev-mcp-v{jev_version}#" in args
        assert jev["env"] == {"TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"}

        front = _frontmatter((root / "skills" / skill / "SKILL.md").read_text())
        assert front["name"] == skill
        assert str(front["description"]).strip()

        assert not [name for name in NEVER_SHIPPED if (root / name).exists()]
