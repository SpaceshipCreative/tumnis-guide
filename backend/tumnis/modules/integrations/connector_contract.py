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

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.modules.integrations.api import Connector, RawItem, connector_adapter_name

__all__ = ["ConnectorContract", "Recording", "load_recordings"]


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
        raise NotImplementedError

    def test_map_matches_expected(self, subject: Connector, case: Recording) -> None:
        raise NotImplementedError

    def test_map_is_pure(self, subject: Connector, case: Recording) -> None:
        raise NotImplementedError

    def test_declares_kind_and_capabilities(self, subject: Connector) -> None:
        raise NotImplementedError

    async def test_sync_pages_terminate(self, subject: Connector) -> None:
        raise NotImplementedError
