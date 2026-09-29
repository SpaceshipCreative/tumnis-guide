"""`AdapterContract`: one set of cases per port, run against every implementation (P0-09).

Subclass once per port without a `Test` prefix (pytest does not collect it), setting
`port` and `adapter_name` and writing the cases; then once per implementation with a
`Test` prefix, setting `impl` and overriding the `subject` fixture. A fake that drifts from
the real contract fails its own class ([ContractTest](https://martinfowler.com/bliki/ContractTest.html)).

Imports pytest, so only tests may import this module (import-linter contract
`contract-base-test-only`).
"""

import abc
from typing import Any, ClassVar, Literal, get_args

import pytest

from tumnis.core.adapters.registry import contract_impls, record_contract

__all__ = ["AdapterContract", "Impl", "contract_impls"]

Impl = Literal["fake", "real", "recorded"]


class AdapterContract[P](abc.ABC):
    port: ClassVar[type[Any]]
    impl: ClassVar[Impl]
    adapter_name: ClassVar[str]  # registry name, e.g. "decisions.jev"

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if not cls.__name__.startswith("Test"):
            return
        impl = getattr(cls, "impl", None)
        if not isinstance(impl, str) or impl not in get_args(Impl):
            raise TypeError(f"{cls.__qualname__}.impl must be one of {get_args(Impl)}")
        name = getattr(cls, "adapter_name", None)
        if not isinstance(name, str) or not name:
            raise TypeError(f"{cls.__qualname__} needs an adapter_name")
        record_contract(name, impl)

    @pytest.fixture
    @abc.abstractmethod
    def subject(self) -> P: ...
