"""The release's asset check (P4-06, SEC-7, SAAS-1): after `gh release create`, the release
job's `verify-assets` step reads the release back and fails unless the CycloneDX SBOM, the
OpenAPI spec and the JSON Schemas archive are all attached and not empty
(scripts/release/verify_assets.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
RELEASE_YML = REPO / ".github" / "workflows" / "release.yml"
VERIFY = REPO / "scripts" / "release" / "verify_assets.py"


def _verify() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_assets", VERIFY)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_assets"] = module
    spec.loader.exec_module(module)
    return module


def _release_steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = yaml.safe_load(RELEASE_YML.read_text())["jobs"]["release"][
        "steps"
    ]
    return steps


def _asset(name: str, size: int = 1024) -> dict[str, Any]:
    return {"name": name, "size": size}


@pytest.mark.req("SEC-7", "SAAS-1")
@pytest.mark.wp("P4-06")
def test_release_fails_without_sbom_openapi_or_schemas() -> None:
    """T-P4-06-07
    release.yml packs schemas/ into schemas-<tag>.tar.gz and attaches it with the SBOM and
    the OpenAPI spec; a `verify-assets` step after `gh release create` runs
    scripts/release/verify_assets.py on the release's assets, which names every one of
    the three that is missing or empty.
    """
    steps = _release_steps()
    runs = [str(step.get("run", "")) for step in steps]
    create = next(i for i, run in enumerate(runs) if "gh release create" in run)
    pack = next(i for i, run in enumerate(runs) if "schemas-${GITHUB_REF_NAME}.tar.gz" in run)
    assert pack < create
    assert "schemas-${GITHUB_REF_NAME}.tar.gz" in runs[create]
    verify = next(i for i, step in enumerate(steps) if step.get("id") == "verify-assets")
    assert verify > create
    assert "scripts/release/verify_assets.py" in runs[verify]
    assert "gh release view" in runs[verify]

    check = _verify()
    tag = "v1.0.0"
    complete = [
        _asset("sbom.cdx.json"),
        _asset("openapi-v1.0.0.json"),
        _asset("schemas-v1.0.0.tar.gz"),
    ]
    assert check.missing_assets(tag, complete) == []
    for gone in range(3):
        rest = [asset for i, asset in enumerate(complete) if i != gone]
        assert check.missing_assets(tag, rest) == [complete[gone]["name"]]
    empty = [*complete[:2], _asset("schemas-v1.0.0.tar.gz", size=0)]
    assert check.missing_assets(tag, empty) == ["schemas-v1.0.0.tar.gz"]
    assert check.missing_assets(tag, [_asset("openapi-v0.9.0.json")]) == [
        "sbom.cdx.json",
        "openapi-v1.0.0.json",
        "schemas-v1.0.0.tar.gz",
    ]
