"""Harness self tests, unit layer: config, clock, adapter switch, seed and load sets (P0-02)."""

from __future__ import annotations

import socket
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

if TYPE_CHECKING:
    from tests.fixtures import Fakes
    from tumnis.core.clock import FixedClock
    from tumnis.seed import InMemorySink

BACKEND = Path(__file__).resolve().parents[2]
SEED_DIR = BACKEND / "fixtures" / "seed"
LOAD_FILE = BACKEND / "fixtures" / "load" / "load.yaml"
ANCHOR = date(2026, 3, 9)
SEED_TZ = ZoneInfo("America/New_York")


def _use_project_config(pytester: pytest.Pytester) -> None:
    """Give the inner pytest run this project's own pytest configuration."""
    pytester.makefile(".toml", pyproject=(BACKEND / "pyproject.toml").read_text())


# --- T-P0-02-08, 09: strict xfail and strict markers ------------------------------------


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
def test_xpass_under_strict_xfail_fails_the_run(pytester: pytest.Pytester) -> None:
    """T-P0-02-08
    A pytester run of a passing xfail test reports a failure, both with an explicit
    strict=True and with the bare marker (xfail_strict = true in the project config).
    """
    _use_project_config(pytester)
    path = pytester.makepyfile(
        test_xpass="""
        import pytest

        @pytest.mark.xfail(strict=True, reason="spec:demo")
        def test_explicit_strict():
            pass

        @pytest.mark.xfail(reason="spec:demo")
        def test_config_strict():
            pass
        """
    )
    result = pytester.runpytest(str(path))
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(["*XPASS(strict)*"])
    assert result.ret == pytest.ExitCode.TESTS_FAILED


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
def test_unregistered_marker_fails_collection(pytester: pytest.Pytester) -> None:
    """T-P0-02-09
    A pytester run with @pytest.mark.bogus errors (strict markers).
    """
    _use_project_config(pytester)
    path = pytester.makepyfile(
        test_bogus="""
        import pytest

        @pytest.mark.bogus
        def test_marked():
            pass
        """
    )
    result = pytester.runpytest(str(path))
    assert result.ret != pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*'bogus' not found in `markers` configuration option*"])


# --- T-P0-02-07: socket block ---------------------------------------------------------


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
def test_unit_test_cannot_open_network_socket() -> None:
    """T-P0-02-07
    Opening a TCP socket raises SocketBlockedError, both directly and through httpx
    (blocked, not refused).
    """
    import httpx  # noqa: PLC0415
    from pytest_socket import SocketBlockedError  # noqa: PLC0415

    with pytest.raises(SocketBlockedError):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(SocketBlockedError):
        httpx.get("http://127.0.0.1:9")


# --- T-P0-02-12: clock ----------------------------------------------------------------


@pytest.mark.req("REL-6")
@pytest.mark.wp("P0-02")
def test_fixed_clock_advances_and_rejects_naive(clock: FixedClock) -> None:
    """T-P0-02-12
    FixedClock starts at 2026-03-09T12:00Z, advance(minutes=5) works, naive input raises.
    """
    from tumnis.core.clock import FixedClock, SystemClock  # noqa: PLC0415

    start = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    assert clock.now() == start
    assert clock.now().utcoffset() == timedelta(0)

    assert clock.advance(minutes=5) == start + timedelta(minutes=5)
    assert clock.now() == start + timedelta(minutes=5)
    assert clock.advance(timedelta(hours=1)) == start + timedelta(hours=1, minutes=5)

    clock.set(datetime(2026, 3, 10, 8, 0, tzinfo=SEED_TZ))
    assert clock.now() == datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assert clock.now().tzinfo == UTC

    with pytest.raises(ValueError, match="aware"):
        FixedClock(datetime(2026, 3, 9, 12, 0))  # noqa: DTZ001
    assert FixedClock(datetime(2026, 3, 9, 8, 0, tzinfo=SEED_TZ)).now() == start

    system_now = SystemClock().now()
    assert system_now.tzinfo == UTC


# --- T-P0-02-04, 05, 06: adapter registry and the fakes switch -------------------------


@runtime_checkable
class Greeter(Protocol):
    def greet(self, name: str) -> str: ...


class RealGreeter:
    def greet(self, name: str) -> str:
        return f"hello {name}"


class FakeGreeter:
    def greet(self, name: str) -> str:
        return f"fake {name}"


def make_fake_greeter() -> FakeGreeter:
    return FakeGreeter()


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real registry after wiring, copied so a test's registrations do not leak."""
    import tumnis.wiring  # noqa: F401, PLC0415
    from tumnis.core.adapters import registry  # noqa: PLC0415

    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    return registry


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-02")
def test_fake_mode_resolves_every_adapter_to_its_fake(
    isolated_registry: Any, fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P0-02-04
    With TUMNIS_ADAPTERS=fake, resolve(name, "fake") returns the fake for every registered spec.
    """
    registry = isolated_registry
    registry.register_adapter("demo.greeter", port=Greeter, real=RealGreeter, fake=FakeGreeter)
    registry.register_adapter(
        "demo.factory", port=Greeter, real=RealGreeter, fake=make_fake_greeter
    )
    monkeypatch.setenv("TUMNIS_ADAPTERS", "fake")
    mode = registry.current_mode()
    assert mode == "fake"

    for spec in registry.registered():
        assert spec.fake is not None, spec.name
        product = registry.resolve(spec.name, mode)
        expected = spec.fake if isinstance(spec.fake, type) else type(spec.fake())
        assert type(product) is expected, spec.name

    assert registry.resolve("demo.greeter", mode).greet("x") == "fake x"
    assert registry.resolve("demo.factory", mode).greet("x") == "fake x"
    assert registry.resolve("demo.greeter", "real").greet("x") == "hello x"
    with pytest.raises(KeyError):
        registry.resolve("demo.unknown", mode)

    assert isinstance(fakes["demo.greeter"], FakeGreeter)
    assert fakes["demo.greeter"] is fakes["demo.greeter"]  # built once per test

    monkeypatch.setenv("TUMNIS_ADAPTERS", "real")
    assert registry.current_mode() == "real"
    monkeypatch.delenv("TUMNIS_ADAPTERS")
    assert registry.current_mode() == "real"
    monkeypatch.setenv("TUMNIS_ADAPTERS", "bogus")
    with pytest.raises(ValueError, match="TUMNIS_ADAPTERS"):
        registry.current_mode()


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-02")
def test_adapter_without_fake_is_a_violation(isolated_registry: Any) -> None:
    """T-P0-02-05
    validate() reports a spec registered with fake=None, and the registry-wide test fails on it.
    """
    registry = isolated_registry
    registry.register_adapter("demo.nofake", port=Greeter, real=RealGreeter, fake=None)

    violations = registry.validate()
    assert any("demo.nofake" in violation for violation in violations), violations
    with pytest.raises(AssertionError):
        test_every_registered_adapter_has_a_fake()


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-02")
def test_every_registered_adapter_has_a_fake() -> None:
    """T-P0-02-06
    Over the real registry after the tumnis.wiring import, validate() == [].
    """
    import tumnis.wiring  # noqa: F401, PLC0415
    from tumnis.core.adapters.registry import validate  # noqa: PLC0415

    assert validate() == []


# --- T-P0-02-01, 02: seed set ---------------------------------------------------------


def _normalized(sink: InMemorySink) -> list[tuple[str, str, dict[str, str | None], Any]]:
    """Records with every UUID replaced by the seed key it was minted for."""
    key_of: dict[UUID, str] = {stored.id: stored.rec.key for stored in sink.records}
    return [
        (
            stored.kind,
            stored.rec.key,
            {name: key_of[ref] if ref else None for name, ref in stored.parents.items()},
            stored.rec.model_dump(),
        )
        for stored in sink.records
    ]


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
@pytest.mark.xfail(strict=True, reason="spec:P0-02")
async def test_seed_parses_three_projects_thirty_tasks_one_day(clock: FixedClock) -> None:
    """T-P0-02-01
    Seed YAML yields 3 projects, 30 tasks, 1 calendar day into InMemorySink.
    """
    from tumnis.seed import InMemorySink, load_seed  # noqa: PLC0415

    sink = InMemorySink()
    result = await load_seed(SEED_DIR, sink, anchor=ANCHOR, clock=clock)

    def of(kind: str) -> list[Any]:
        return [stored for stored in sink.records if stored.kind == kind]

    assert len(of("workspace")) == 1
    assert len(of("user")) == 1
    assert {p.rec.key for p in of("project")} == {"p_acme", "p_authenticity", "p_dogfood"}
    assert len(of("task")) == 30
    assert len(of("document")) == 3
    events = of("event")
    assert len(events) == 6
    assert {e.rec.start_at.astimezone(SEED_TZ).date() for e in events} == {ANCHOR}
    assert all(e.rec.start_at.tzinfo is not None for e in events)
    assert result.counts == {
        "workspace": 1,
        "user": 1,
        "project": 3,
        "task": 30,
        "event": 6,
        "document": 3,
    }
    assert set(result.ids) == {stored.rec.key for stored in sink.records}

    tasks = [t.rec for t in of("task")]
    statuses = {t.status for t in tasks}
    assert {"backlog", "today", "in_progress", "waiting_on_human", "in_review", "done"} <= statuses
    assert {t.label for t in tasks} == {"human", "ai", "hybrid", None}
    assert any(t.due_on is not None and t.due_on < ANCHOR and t.status != "done" for t in tasks)

    dogfood = next(p.id for p in of("project") if p.rec.key == "p_dogfood")
    subtask_minutes = sorted(
        t.rec.estimate_minutes
        for t in of("task")
        if t.parents["project"] == dogfood and t.parents["parent"] is not None
    )
    assert {29, 30, 31} <= set(subtask_minutes)


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
@pytest.mark.xfail(strict=True, reason="spec:P0-02")
async def test_two_seed_loads_identical_apart_from_ids(clock: FixedClock) -> None:
    """T-P0-02-02
    Two loads into two sinks produce equal records after replacing UUIDs with seed keys.
    """
    from tumnis.seed import InMemorySink, load_seed  # noqa: PLC0415

    first, second = InMemorySink(), InMemorySink()
    await load_seed(SEED_DIR, first, anchor=ANCHOR, clock=clock)
    await load_seed(SEED_DIR, second, anchor=ANCHOR, clock=clock)

    assert {s.id for s in first.records}.isdisjoint({s.id for s in second.records})
    assert _normalized(first) == _normalized(second)
    assert len(_normalized(first)) == 1 + 1 + 3 + 30 + 6 + 3


# --- T-P0-02-13: load generator -------------------------------------------------------


@pytest.mark.req("PERF-2")
@pytest.mark.wp("P0-02")
@pytest.mark.xfail(strict=True, reason="spec:P0-02")
def test_load_generator_is_deterministic() -> None:
    """T-P0-02-13
    generate(projects=10, tasks=2000, seed=42) twice gives equal output; counts match;
    the committed load.yaml is that output.
    """
    import yaml  # noqa: PLC0415

    from fixtures.load.generate import generate  # noqa: PLC0415

    first = generate(projects=10, tasks=2000, seed=42)
    second = generate(projects=10, tasks=2000, seed=42)
    assert first == second
    assert generate(projects=10, tasks=2000, seed=43) != first

    def count(tasks: list[dict[str, Any]]) -> int:
        return sum(1 + count(task.get("subtasks", [])) for task in tasks)

    assert len(first["projects"]) == 10
    assert sum(count(project["tasks"]) for project in first["projects"]) == 2000
    assert yaml.safe_load(LOAD_FILE.read_text()) == first
