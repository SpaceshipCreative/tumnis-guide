"""The shared contract-suite base: one set of cases runs against every implementation (P0-09).

A pytester run builds a demo port `Shout`: `RealShout` upper-cases, `GoodFake` upper-cases
and `BadFake` lower-cases, so the bad fake drifts from the real contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[5]

DEMO = """
from typing import Protocol

import pytest

from tumnis.core.adapters.contract import AdapterContract


class Shout(Protocol):
    def shout(self, text: str) -> str: ...


class RealShout:
    def shout(self, text: str) -> str:
        return text.upper()


class GoodFake:
    def shout(self, text: str) -> str:
        return text.upper()


class BadFake:
    def shout(self, text: str) -> str:
        return text.lower()


class ShoutContract(AdapterContract[Shout]):
    port = Shout
    adapter_name = "demo.shout"

    def test_shouts_in_upper_case(self, subject: Shout) -> None:
        assert subject.shout("Hi there") == "HI THERE"


@pytest.mark.contract
class TestRealShout(ShoutContract):
    impl = "real"

    @pytest.fixture
    def subject(self) -> Shout:
        return RealShout()


@pytest.mark.contract
class Test{fake}(ShoutContract):
    impl = "fake"

    @pytest.fixture
    def subject(self) -> Shout:
        return {fake}()
"""


@pytest.fixture
def contracts(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The contract bookkeeping, emptied for this test; the inner run uses the project config."""
    import tumnis.core.adapters.contract  # noqa: F401, PLC0415 (loaded once, outside the run)
    from tumnis.core.adapters import registry  # noqa: PLC0415

    monkeypatch.setattr(registry, "_CONTRACTS", {})
    pytester.makefile(".toml", pyproject=(BACKEND / "pyproject.toml").read_text())
    return registry


@pytest.mark.req("PRD Testability NFR")
@pytest.mark.wp("P0-09")
def test_same_cases_run_against_each_impl(pytester: pytest.Pytester, contracts: Any) -> None:
    """T-P0-09-11
    A pytester run of a demo port with fake and real subclasses collects the same case names
    under both classes, all passing, and records both implementations for the adapter.
    """
    path = pytester.makepyfile(test_shout_contract=DEMO.replace("{fake}", "GoodFake"))
    result = pytester.runpytest(str(path), "-v")
    result.assert_outcomes(passed=2, failed=0)
    result.stdout.fnmatch_lines_random(
        [
            "*::TestRealShout::test_shouts_in_upper_case PASSED*",
            "*::TestGoodFake::test_shouts_in_upper_case PASSED*",
        ]
    )
    assert contracts.contract_impls()["demo.shout"] == {"real", "fake"}


@pytest.mark.req("PRD Testability NFR")
@pytest.mark.wp("P0-09")
def test_divergent_fake_fails_the_contract(pytester: pytest.Pytester, contracts: Any) -> None:
    """T-P0-09-12
    The same demo with a fake that lower-cases where the real upper-cases fails only in the
    fake class; both classes are still recorded for the adapter.
    """
    path = pytester.makepyfile(test_shout_contract=DEMO.replace("{fake}", "BadFake"))
    result = pytester.runpytest(str(path), "-v")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["FAILED *::TestBadFake::test_shouts_in_upper_case*"])
    result.stdout.no_fnmatch_line("FAILED *::TestRealShout::*")
    assert contracts.contract_impls()["demo.shout"] == {"real", "fake"}
