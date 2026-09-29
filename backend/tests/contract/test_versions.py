"""Every payload carries an integer `schema_version` const, and accepts its current version
and the one before it (P0-11, FR-14.7, REL-4, R-02).

The sweep runs over the schema registry after every payload model has been imported, so a
payload a later work package registers is checked with no edit here: it needs its fixtures
under `backend/tests/contract/fixtures/<family>/<name>/v<N>.json` (and v<N-1> with an
upgrader once it reaches v2).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

import pytest

from tumnis.core.schemas import VersionedPayload

if TYPE_CHECKING:
    from pathlib import Path

FIXTURES = "backend/tests/contract/fixtures"


def _latest() -> list[Any]:
    """The latest version of each published payload, as test parameters."""
    try:
        from tumnis.core.schemas import registry  # noqa: PLC0415
        from tumnis.gen import load_all  # noqa: PLC0415
    except ImportError:  # the registry arrives with P0-11
        return [pytest.param(None, id="registry-pending")]
    load_all()
    return [pytest.param(spec, id=f"{spec.family}/{spec.name}") for spec in registry().latest()]


def _fixture(repo_root: Path, family: str, name: str, version: int) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (repo_root / FIXTURES / family / name / f"v{version}.json").read_text()
    )
    return data


@pytest.mark.contract
@pytest.mark.req("FR-14.7")
@pytest.mark.wp("P0-11")
def test_every_payload_has_schema_version_const() -> None:
    """T-P0-11-05
    Each registered schema has `properties.schema_version.const == version`, and
    `schema_version` is required. The event envelope's first entries are there: the problem
    model and the P0-07 example event.
    """
    from tumnis.core.schemas import json_schema, registry  # noqa: PLC0415
    from tumnis.gen import load_all  # noqa: PLC0415

    load_all()
    specs = registry().published()
    assert {("api", "problem"), ("events", "test.ping")} <= {(s.family, s.name) for s in specs}
    for spec in specs:
        schema = json_schema(spec)
        assert schema["properties"]["schema_version"]["const"] == spec.version, spec
        assert "schema_version" in schema["required"], spec


@pytest.mark.contract
@pytest.mark.req("FR-14.7", "REL-4")
@pytest.mark.wp("P0-11")
@pytest.mark.xfail(strict=True, reason="spec:P0-11")
@pytest.mark.parametrize("spec", _latest())
def test_every_payload_accepts_n_and_n_minus_1(spec: Any, repo_root: Path) -> None:
    """T-P0-11-06
    The v(N) fixture parses; when N > 1 the v(N-1) fixture parses and upgrades to the v(N)
    model; v(N-2) raises UnsupportedSchemaVersion.
    """
    from tumnis.core.schemas import UnsupportedSchemaVersion, parse_versioned  # noqa: PLC0415

    family, name, n = spec.family, spec.name, spec.version
    current = _fixture(repo_root, family, name, n)
    parsed = parse_versioned(family, name, current)
    assert isinstance(parsed, spec.model)
    assert parsed.schema_version == n
    if n > 1:
        upgraded = parse_versioned(family, name, _fixture(repo_root, family, name, n - 1))
        assert isinstance(upgraded, spec.model)
        assert upgraded.schema_version == n
    with pytest.raises(UnsupportedSchemaVersion):
        parse_versioned(family, name, {**current, "schema_version": n - 2})


@pytest.mark.contract
@pytest.mark.req("FR-14.7")
@pytest.mark.wp("P0-11")
@pytest.mark.xfail(strict=True, reason="spec:P0-11")
def test_every_schema_has_fixtures_and_upgraders(repo_root: Path) -> None:
    """T-P0-11-07
    Fixture files exist for N (and N-1 when N > 1); an upgrader exists from N-1.
    """
    from tumnis.core.schemas import registry  # noqa: PLC0415
    from tumnis.gen import load_all  # noqa: PLC0415

    load_all()
    reg = registry()
    missing: list[str] = []
    for spec in reg.latest():
        versions = [spec.version, spec.version - 1] if spec.version > 1 else [spec.version]
        for version in versions:
            path = f"{FIXTURES}/{spec.family}/{spec.name}/v{version}.json"
            if not (repo_root / path).is_file():
                missing.append(path)
        if spec.version > 1 and reg.upgrader(spec.family, spec.name, spec.version - 1) is None:
            missing.append(f"upgrader {spec.family}/{spec.name} v{spec.version - 1}")
    assert missing == []


class DemoV1(VersionedPayload):
    schema_version: Literal[1] = 1
    title: str


class DemoV2(VersionedPayload):
    schema_version: Literal[2] = 2
    name: str
    tags: tuple[str, ...] = ()


@pytest.mark.req("REL-4")
@pytest.mark.wp("P0-11")
def test_upgrade_mechanism_on_demo_family() -> None:
    """T-P0-11-08
    A demo payload with v1 and v2 and an upgrader from v1 shows the full path: v2 parses,
    v1 parses and upgrades to v2, anything else raises UnsupportedSchemaVersion; a model
    whose schema_version Literal disagrees with its registration is refused, and so is a
    second registration of the same (family, name, version).
    """
    from tumnis.core.schemas import (  # noqa: PLC0415
        SchemaRegistry,
        UnsupportedSchemaVersion,
        parse_versioned,
        upgrader,
        use_registry,
        versioned,
    )

    with use_registry(SchemaRegistry()):
        versioned("api", "demo", 1)(DemoV1)
        versioned("api", "demo", 2)(DemoV2)

        @upgrader("api", "demo", 1)
        def title_to_name(data: dict[str, Any]) -> dict[str, Any]:
            out = dict(data)
            out["name"] = out.pop("title")
            return out

        assert parse_versioned("api", "demo", {"schema_version": 2, "name": "a"}) == DemoV2(
            name="a"
        )
        assert parse_versioned("api", "demo", {"schema_version": 1, "title": "b"}) == DemoV2(
            name="b"
        )
        for version in (0, 3, "2", None):
            with pytest.raises(UnsupportedSchemaVersion):
                parse_versioned("api", "demo", {"schema_version": version, "name": "c"})
        with pytest.raises(UnsupportedSchemaVersion):
            parse_versioned("api", "demo", {"name": "no version"})
        with pytest.raises(ValueError, match="Literal"):
            versioned("api", "demo", 3)(DemoV2)
        with pytest.raises(ValueError, match="demo"):
            versioned("api", "demo", 2)(DemoV2)
