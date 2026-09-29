"""`ConnectorContract`: the shared contract suite every connector passes (P0-12, FR-14.5).

A connector's contract test is a folder of recorded payloads and the canonical records
they must map to ([ContractTest](https://martinfowler.com/bliki/ContractTest.html)).
Subclass per provider, setting `provider` and `recordings_dir` and overriding `subject`;
one case per `<case>.json` in the folder:

    {"raw": <RawItem>, "expected": [<CanonicalRecord as JSON>, ...], "notes": "Scrubbed: ..."}

Dropping one more file into the folder adds one case. The suite runs in the contract
layer with sockets disabled, so a `map` that reaches the network fails too.

Imports pytest, so only tests may import this module (import-linter:
`tumnis.modules.*.tests.** -> tumnis.modules.integrations.connector_contract` is the one
sanctioned exception to module independence).
"""

# ruff: noqa: S101  # a test suite (only tests import it): its cases assert

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, get_args

import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.modules.integrations.api import (
    Capability,
    Connector,
    ConnectorKind,
    RawItem,
    connector_adapter_name,
)

__all__ = ["ConnectorContract", "Recording", "load_recordings"]

MAX_PAGES = 1000  # a sync that has not finished by then never will


@dataclass(frozen=True)
class Recording:
    name: str  # the file stem, the case id
    raw: RawItem
    expected: list[dict[str, Any]]


def load_recordings(folder: Path) -> list[Recording]:
    """Every `<case>.json` in `folder`, sorted by name."""
    cases = []
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text())
        cases.append(Recording(path.stem, RawItem.model_validate(data["raw"]), data["expected"]))
    return cases


class ConnectorContract(AdapterContract[Connector]):
    """Subclass per provider: set `provider`, `recordings_dir`, override `subject`. Cases are
    generated from every <case>.json in the folder: {"raw": RawItem, "expected": [...]}."""

    port = Connector
    provider: ClassVar[str]
    recordings_dir: ClassVar[Path]

    def __init_subclass__(cls, **kw: Any) -> None:
        provider = getattr(cls, "provider", None)
        if isinstance(provider, str) and "adapter_name" not in cls.__dict__:
            cls.adapter_name = connector_adapter_name(provider)
        super().__init_subclass__(**kw)

    def pytest_generate_tests(self, metafunc: pytest.Metafunc) -> None:
        if "case" in metafunc.fixturenames:
            cases = load_recordings(self.recordings_dir)
            metafunc.parametrize("case", cases, ids=[case.name for case in cases])

    def test_has_recordings(self) -> None:
        """A connector's contract needs at least one recording."""
        assert load_recordings(self.recordings_dir), f"no recordings in {self.recordings_dir}"

    def test_map_matches_expected(self, subject: Connector, case: Recording) -> None:
        """The recorded payload maps to exactly the expected canonical records."""
        got = [r.model_dump(mode="json") for r in subject.map(case.raw)]
        assert got == case.expected

    def test_map_is_pure(self, subject: Connector, case: Recording) -> None:
        """Same input, same output, input untouched; with sockets disabled, no I/O."""
        raw = case.raw.model_copy(deep=True)
        runs = [subject.map(case.raw.model_copy(deep=True)) for _ in range(3)]
        assert all(run == runs[0] for run in runs)
        assert subject.map(raw) == runs[0]
        assert raw == case.raw

    def test_declares_kind_and_capabilities(self, subject: Connector) -> None:
        """The kind and capabilities come from the closed vocabularies; the provider matches."""
        assert subject.provider == self.provider
        assert subject.kind in get_args(ConnectorKind)
        assert subject.capabilities, "a connector declares at least one capability"
        assert subject.capabilities <= set(get_args(Capability))

    async def test_sync_pages_terminate(self, subject: Connector) -> None:
        """Following next_cursor, has_more eventually turns False."""
        cursor: dict[str, Any] | None = None
        for _ in range(MAX_PAGES):
            page = await subject.sync(cursor)
            if not page.has_more:
                return
            cursor = page.next_cursor
        pytest.fail(f"sync still has more after {MAX_PAGES} pages")
