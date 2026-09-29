"""`AdapterContract`: one set of cases per port, run against every implementation (P0-09).

Imports pytest, so only tests may import this module (import-linter contract
`contract-base-test-only`).
"""

import abc
from collections.abc import Mapping
from typing import Any, ClassVar, Literal

import pytest

Impl = Literal["fake", "real", "recorded"]


class AdapterContract[P](abc.ABC):
    port: ClassVar[type[Any]]
    impl: ClassVar[Impl]
    adapter_name: ClassVar[str]

    @pytest.fixture
    @abc.abstractmethod
    def subject(self) -> P: ...


def contract_impls() -> Mapping[str, set[str]]:
    raise NotImplementedError
