"""Versioned payload models and their JSON Schemas (R-02, FR-14.7, REL-4, ADR-0003).

Every payload that crosses a process or agent boundary is a `VersionedPayload` whose
`schema_version` is an integer `Literal`, registered with `@versioned(family, name, version)`.
The registry is the one source of the committed schemas: `write_all(root)` writes
`schemas/<family>/v<N>/<name>.json` for each, and `check(root)` lists the files that no
longer match the code. `parse_versioned` accepts the latest version N and N-1 (upgraded
through the `@upgrader` registered from N-1) for one release, and refuses anything else.

    @versioned("events", "task.created", 1)
    class TaskCreatedV1(EventPayload):
        schema_version: Literal[1] = 1
        ...
"""

import json
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, TypeVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict

Family = Literal[
    "entities", "packet", "planning", "enrichment", "result", "digest", "runner", "events", "api"
]
FAMILIES: Final[tuple[str, ...]] = get_args(Family)
SCHEMAS_DIR: Final = "schemas"  # under the repository root
DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
ID_BASE: Final = "https://tumnis.dev/schemas/"
_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class VersionedPayload(BaseModel):
    """Base for every payload that crosses a process or a release: frozen, no unknown
    fields, and an integer `schema_version` that each subclass pins with
    `schema_version: Literal[<n>] = <n>`, so its JSON Schema carries a const."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int


M = TypeVar("M", bound=type[VersionedPayload])
UpgradeFn = Callable[[dict[str, Any]], dict[str, Any]]
U = TypeVar("U", bound=UpgradeFn)


@dataclass(frozen=True)
class SchemaSpec:
    family: Family
    name: str  # "task.created", "task_packet", "problem"
    version: int
    model: type[VersionedPayload]

    @property
    def path(self) -> str:
        """The schema file, relative to the repository root."""
        return f"{SCHEMAS_DIR}/{self.family}/v{self.version}/{self.name}.json"

    @property
    def published(self) -> bool:
        """A model defined inside a function (a test's throwaway) can be registered and
        parsed, but it is never written to `schemas/`."""
        return "<locals>" not in self.model.__qualname__


class UnsupportedSchemaVersion(ValueError):  # noqa: N818  # the plan's name
    """A payload whose schema_version is neither the latest (N) nor N-1."""

    code: Final = "unsupported_schema_version"

    def __init__(self, family: str, name: str, version: object, accepted: tuple[int, ...]):
        super().__init__(
            f"{family}/{name}: schema_version {version!r} is not supported "
            f"(accepted: {', '.join(map(str, accepted)) or 'none'})"
        )
        self.family, self.name, self.version, self.accepted = family, name, version, accepted


class SchemaRegistry:
    """(family, name, version) -> model, and (family, name, from_version) -> upgrader."""

    def __init__(self) -> None:
        self._specs: dict[tuple[str, str, int], SchemaSpec] = {}
        self._upgraders: dict[tuple[str, str, int], UpgradeFn] = {}

    def register(self, spec: SchemaSpec) -> None:
        key = (spec.family, spec.name, spec.version)
        existing = self._specs.get(key)
        if existing is not None:
            raise ValueError(
                f"schema {spec.family}/{spec.name} v{spec.version} is already registered "
                f"by {existing.model.__qualname__}"
            )
        self._specs[key] = spec

    def register_upgrader(self, family: str, name: str, from_version: int, fn: UpgradeFn) -> None:
        key = (family, name, from_version)
        if key in self._upgraders:
            raise ValueError(f"upgrader {family}/{name} v{from_version} is already registered")
        self._upgraders[key] = fn

    def specs(self) -> list[SchemaSpec]:
        return [self._specs[key] for key in sorted(self._specs)]

    def published(self) -> list[SchemaSpec]:
        """Every spec written to `schemas/`, sorted by family, name and version."""
        return [spec for spec in self.specs() if spec.published]

    def latest(self) -> list[SchemaSpec]:
        """The latest published version of each (family, name)."""
        by_name = {(s.family, s.name): s for s in self.published()}  # sorted: last wins
        return [by_name[key] for key in sorted(by_name)]

    def versions(self, family: str, name: str) -> dict[int, SchemaSpec]:
        return {v: s for (f, n, v), s in sorted(self._specs.items()) if (f, n) == (family, name)}

    def upgrader(self, family: str, name: str, from_version: int) -> UpgradeFn | None:
        return self._upgraders.get((family, name, from_version))


_registry = SchemaRegistry()


def registry() -> SchemaRegistry:
    return _registry


@contextmanager
def use_registry(reg: SchemaRegistry) -> Iterator[SchemaRegistry]:
    """Swap the registry (tests): `versioned`, `parse_versioned`, `write_all` and `check`
    use `reg` until the block ends."""
    global _registry  # noqa: PLW0603  # one process-wide registry, swapped only by tests
    previous, _registry = _registry, reg
    try:
        yield reg
    finally:
        _registry = previous


def _declared_version(model: type[VersionedPayload]) -> tuple[int, ...]:
    annotation = model.model_fields["schema_version"].annotation
    return get_args(annotation) if get_origin(annotation) is Literal else ()


def versioned(family: Family, name: str, version: int) -> Callable[[M], M]:
    """Registers the model. (family, name, version) is unique, and the model's
    schema_version must be `Literal[version]`."""
    if family not in FAMILIES:
        raise ValueError(f"unknown schema family {family!r}; one of {FAMILIES}")
    if not _NAME.match(name):
        raise ValueError(f"schema name {name!r} is not dotted snake_case")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"schema {family}/{name}: version {version!r} is not an int >= 1")

    def register(model: M) -> M:
        if not (isinstance(model, type) and issubclass(model, VersionedPayload)):
            raise TypeError(f"{model!r} is not a VersionedPayload")
        if _declared_version(model) != (version,):
            raise ValueError(
                f"{model.__qualname__} must declare schema_version: Literal[{version}] "
                f"to register as {family}/{name} v{version}"
            )
        _registry.register(SchemaSpec(family, name, version, model))
        return model

    return register


def upgrader(family: Family, name: str, from_version: int) -> Callable[[U], U]:
    """Registers data -> data for from_version -> from_version + 1. The upgraded data gets
    `schema_version = from_version + 1` whatever the function returns for it."""

    def register(fn: U) -> U:
        _registry.register_upgrader(family, name, from_version, fn)
        return fn

    return register


def parse_versioned(family: Family, name: str, data: Mapping[str, Any]) -> VersionedPayload:
    """Accepts the latest version N and N-1 (validated against its own model when that is
    still registered, then upgraded through its upgrader); anything else raises
    UnsupportedSchemaVersion (problem code unsupported_schema_version)."""
    versions = _registry.versions(family, name)
    if not versions:
        raise LookupError(f"no schema registered as {family}/{name}")
    latest = max(versions)
    accepted = (latest - 1, latest) if _registry.upgrader(family, name, latest - 1) else (latest,)
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in accepted:
        raise UnsupportedSchemaVersion(family, name, version, accepted)
    if version == latest:
        return versions[latest].model.model_validate(data)
    if version in versions:
        versions[version].model.model_validate(data)
    upgrade = _registry.upgrader(family, name, version)
    assert upgrade is not None  # noqa: S101  # `accepted` holds N-1 only with an upgrader
    upgraded = {**upgrade(dict(data)), "schema_version": latest}
    return versions[latest].model.model_validate(upgraded)


def json_schema(spec: SchemaSpec) -> dict[str, Any]:
    """The model's JSON Schema (validation mode) with the 2020-12 dialect, a stable `$id`,
    and `schema_version` required: a payload always says which version it is."""
    s = spec.model.model_json_schema(mode="validation")
    s["$schema"] = DIALECT
    s["$id"] = f"{ID_BASE}{spec.family}/v{spec.version}/{spec.name}.json"
    required = s.setdefault("required", [])
    if "schema_version" not in required:
        required.insert(0, "schema_version")
    return s


def dump_json(value: Any) -> str:
    """The one deterministic JSON writer for generated files: sorted keys, two-space
    indent, a final newline."""
    return json.dumps(value, sort_keys=True, indent=2) + "\n"


def expected_files() -> dict[str, str]:
    """Every published schema file (path relative to the repository root -> contents)."""
    return {spec.path: dump_json(json_schema(spec)) for spec in _registry.published()}


def _family_files(root: Path) -> set[str]:
    """The schema files present under `schemas/<family>/v<N>/` (openapi.json and the
    README are not family files)."""
    found: set[str] = set()
    for family in FAMILIES:
        folder = root / SCHEMAS_DIR / family
        if folder.is_dir():
            found |= {
                PurePosixPath(p.relative_to(root)).as_posix() for p in folder.glob("v*/*.json")
            }
    return found


def write_all(out: Path) -> list[Path]:
    """Writes every published schema under `out/schemas/` deterministically (json.dumps
    with sort_keys=True, indent=2, plus "\\n") and removes family files no model produces
    any more. Returns the files written."""
    expected = expected_files()
    for stale in sorted(_family_files(out) - expected.keys()):
        (out / stale).unlink()
    written = []
    for rel, text in sorted(expected.items()):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)
    return written


def diff_files(root: Path, expected: Mapping[str, str], present: set[str]) -> list[str]:
    """Paths (relative to root) that are missing, differ from `expected`, or are in
    `present` without being expected (stale), sorted."""
    out = {rel for rel in present if rel not in expected}
    for rel, text in expected.items():
        path = root / rel
        if not path.is_file() or path.read_text() != text:
            out.add(rel)
    return sorted(out)


def check(root: Path) -> list[str]:
    """The schema files under `root` that do not match the code (missing, different or
    stale), relative to root; empty when the committed tree is current."""
    return diff_files(root, expected_files(), _family_files(root))
