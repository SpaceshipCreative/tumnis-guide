"""The code is the only source of contracts: `tumnis gen` writes the JSON Schemas, the OpenAPI
document and the generated contract tests deterministically, `--check` catches a model
changed without regenerating, and the openapi-ts client matches the committed one (P0-11,
FR-14.7, ADR-0003, SAAS-1, R-19)."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Literal

import pytest

from tumnis.core.schemas import VersionedPayload

if TYPE_CHECKING:
    from pathlib import Path

GENERATED_TESTS = "backend/tests/contract/generated"


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


@pytest.mark.contract
@pytest.mark.req("FR-14.7")
@pytest.mark.wp("P0-11")
def test_generation_is_deterministic(tmp_path: Path) -> None:
    """T-P0-11-01
    Two generation runs into temp dirs give byte-identical trees: the JSON Schemas, the
    OpenAPI document and the generated contract tests.
    """
    from tumnis.gen import generate  # noqa: PLC0415

    first, second = tmp_path / "a", tmp_path / "b"
    generate(first)
    generate(second)
    tree = _tree(first)
    assert tree == _tree(second)
    assert "schemas/openapi.json" in tree
    assert "schemas/api/v1/problem.json" in tree
    assert "schemas/events/v1/test.ping.json" in tree
    assert f"{GENERATED_TESTS}/test_schema_events.py" in tree


@pytest.mark.contract
@pytest.mark.req("FR-14.7", "ADR-0003")
@pytest.mark.wp("P0-11")
def test_committed_schemas_match_code(repo_root: Path, tmp_path: Path) -> None:
    """T-P0-11-02
    `tumnis gen all --check` exits 0 on the committed tree, and exits 1 listing the
    differing files when a generated file was edited by hand.
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tumnis.cli import app  # noqa: PLC0415

    runner = CliRunner()
    clean = runner.invoke(app, ["gen", "all", "--out", str(repo_root), "--check"])
    assert clean.exit_code == 0, clean.output

    for folder in ("schemas", GENERATED_TESTS):
        shutil.copytree(repo_root / folder, tmp_path / folder)
    edited = tmp_path / "schemas/api/v1/problem.json"
    edited.write_text(edited.read_text().replace('"title"', '"heading"'))
    dirty = runner.invoke(app, ["gen", "all", "--out", str(tmp_path), "--check"])
    assert dirty.exit_code == 1, dirty.output
    assert "schemas/api/v1/problem.json" in dirty.output
    assert "schemas/openapi.json" not in dirty.output


class DemoV1(VersionedPayload):
    schema_version: Literal[1] = 1
    title: str


class DemoV1WithField(DemoV1):
    due_on: str | None = None


@pytest.mark.contract
@pytest.mark.req("FR-14.7")
@pytest.mark.wp("P0-11")
def test_field_change_without_regeneration_is_detected(tmp_path: Path) -> None:
    """T-P0-11-03
    Given a registered demo model `Demo v1` written to a tree, when the model gains a field
    (a subclass re-registered under a patched registry), then `check(root)` reports
    `schemas/api/v1/demo.json`.
    """
    from tumnis.core.schemas import (  # noqa: PLC0415
        SchemaRegistry,
        check,
        use_registry,
        versioned,
        write_all,
    )

    with use_registry(SchemaRegistry()):
        versioned("api", "demo", 1)(DemoV1)
        write_all(tmp_path)
        assert check(tmp_path) == []
    with use_registry(SchemaRegistry()):
        versioned("api", "demo", 1)(DemoV1WithField)
        assert check(tmp_path) == ["schemas/api/v1/demo.json"]


@pytest.mark.contract
@pytest.mark.req("ADR-0003")
@pytest.mark.wp("P0-11")
@pytest.mark.xfail(strict=True, reason="spec:P0-11")
def test_openapi_ts_output_matches_committed(repo_root: Path, tmp_path: Path) -> None:
    """T-P0-11-04
    `npx openapi-ts` with its output redirected to a temp dir (OPENAPI_TS_OUTPUT), formatted
    by the pinned Prettier, equals the committed `frontend/src/api`.
    """
    frontend = repo_root / "frontend"
    assert (frontend / "node_modules").is_dir(), "run `npm ci` in frontend/ first"
    assert (frontend / "openapi-ts.config.ts").is_file()
    out = tmp_path / "api"
    env = {**os.environ, "OPENAPI_TS_OUTPUT": str(out)}
    for command in (["openapi-ts"], ["prettier", "--write", str(out)]):
        subprocess.run(  # noqa: S603
            ["npx", "--no-install", *command],  # noqa: S607
            cwd=frontend,
            env=env,
            check=True,
            capture_output=True,
            timeout=180,
        )
    assert _tree(out) == _tree(frontend / "src" / "api")


@pytest.mark.contract
@pytest.mark.req("SAAS-1")
@pytest.mark.wp("P0-11")
@pytest.mark.xfail(strict=True, reason="spec:P0-11")
def test_operation_ids_are_unique_and_stable() -> None:
    """T-P0-11-10
    Every operation ID in the OpenAPI document is unique and equals
    `<first tag>_<function name>` (R-19), so regenerated client names do not churn.
    """
    from tumnis.core.routing import walk_routes  # noqa: PLC0415
    from tumnis.gen import api_app  # noqa: PLC0415

    app = api_app()
    names = {
        (route.path, method): route.original_route.name  # type: ignore[attr-defined]
        for route in walk_routes(app)
        for method in route.methods or ()
    }
    ids: list[str] = []
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            name = names[path, method.upper()]
            tags = operation.get("tags") or []
            assert operation["operationId"] == (f"{tags[0]}_{name}" if tags else name)
            ids.append(operation["operationId"])
    assert len(ids) == len(set(ids))
    assert "usage_get_usage" in ids


@pytest.mark.contract
@pytest.mark.req("FR-14.7")
@pytest.mark.wp("P0-11")
def test_generated_contract_tests_cover_every_schema(repo_root: Path) -> None:
    """T-P0-11-11
    Each registered schema appears in a generated test's CASES, and each generated file
    starts with `# @generated`.
    """
    from tumnis.core.schemas import registry  # noqa: PLC0415
    from tumnis.gen import load_all  # noqa: PLC0415

    load_all()
    files = sorted((repo_root / GENERATED_TESTS).glob("test_schema_*.py"))
    assert files
    covered: set[tuple[str, str, int]] = set()
    for path in files:
        source = path.read_text()
        assert source.startswith("# @generated"), path
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "CASES":
                covered |= {case[:3] for case in ast.literal_eval(node.value)}
    expected = {(s.family, s.name, s.version) for s in registry().published()}
    assert expected <= covered, expected - covered
