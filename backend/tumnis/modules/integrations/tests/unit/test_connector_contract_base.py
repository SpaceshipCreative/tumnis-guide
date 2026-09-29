"""The connector contract base: a connector's contract test is a folder of recordings
(P0-12, FR-14.5).

Pytester runs build small demo connectors against `ConnectorContract`: one file per
recording adds one case, a wrong `expected` fails only its case, and a `map` that reads
the clock or opens a socket fails `test_map_is_pure`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[5]
FETCHED = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)

DEMO = """
import socket
from datetime import datetime
from pathlib import Path

import pytest

from tumnis.modules.integrations.api import MessageRecord, SyncPage
from tumnis.modules.integrations.connector_contract import ConnectorContract

RECORDINGS = Path(__file__).parent / "recordings" / "demo"


class Pure:
    kind = "email"
    provider = "demo"
    capabilities = frozenset({"poll", "read"})

    def __init__(self):
        self._pages = [
            SyncPage(items=[], next_cursor={"page": 2}, has_more=True),
            SyncPage(items=[], next_cursor=None, has_more=False),
        ]

    async def sync(self, cursor):
        return self._pages.pop(0)

    def subject_of(self, raw):
        return raw.payload["subject"]

    def map(self, raw):
        return [
            MessageRecord(
                external_id=raw.external_id,
                fetched_at=raw.fetched_at,
                subject=self.subject_of(raw),
            )
        ]

    async def health(self):
        return "ok"


class ReadsTheClock(Pure):
    def subject_of(self, raw):
        return f"{raw.payload['subject']} at {datetime.now().isoformat()}"


class OpensASocket(Pure):
    def subject_of(self, raw):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("192.0.2.1", 443))
        return raw.payload["subject"]


class DemoContract(ConnectorContract):
    provider = "demo"
    recordings_dir = RECORDINGS


{classes}
"""

CLASS = """
@pytest.mark.contract
class Test{impl}(DemoContract):
    impl = "fake"

    @pytest.fixture
    def subject(self):
        return {impl}()
"""


def _recording(folder: Path, name: str, subject: str, expected_subject: str | None = None) -> None:
    from tumnis.modules.integrations.api import MessageRecord  # noqa: PLC0415

    raw = {
        "external_id": name,
        "record_type": "message",
        "fetched_at": FETCHED.isoformat(),
        "payload": {"subject": subject},
    }
    record = MessageRecord(
        external_id=name, fetched_at=FETCHED, subject=expected_subject or subject
    )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(
        json.dumps({"raw": raw, "expected": [record.model_dump(mode="json")], "notes": "demo"})
    )


@pytest.fixture
def contracts(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The contract bookkeeping, emptied for this test; the inner run uses the project config
    (sockets disabled, as in the unit and contract layers)."""
    import tumnis.modules.integrations.connector_contract  # noqa: F401, PLC0415
    from tumnis.core.adapters import registry  # noqa: PLC0415

    monkeypatch.setattr(registry, "_CONTRACTS", {})
    monkeypatch.setenv("COLUMNS", "400")  # summary lines keep the whole error name
    pytester.makefile(".toml", pyproject=(BACKEND / "pyproject.toml").read_text())
    return registry


def _outcomes(pytester: pytest.Pytester, path: Path) -> tuple[pytest.RunResult, dict[str, int]]:
    result = pytester.runpytest(str(path), "-v")
    return result, result.parseoutcomes()


@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P0-12")
def test_new_recording_file_adds_a_case(pytester: pytest.Pytester, contracts: Any) -> None:
    """T-P0-12-07
    With pytester, dropping one more JSON into the folder adds one parametrized case (to
    each per-recording test); a wrong `expected` fails only that case.
    """
    folder = pytester.path / "recordings" / "demo"
    path = pytester.makepyfile(
        test_demo_contract=DEMO.replace("{classes}", CLASS.format(impl="Pure"))
    )

    _recording(folder, "invoice", "Invoice")
    first, counts = _outcomes(pytester, path)
    assert counts.get("failed", 0) == 0, first.stdout.str()
    first.stdout.fnmatch_lines_random(
        [
            "*::TestPure::test_map_matches_expected[[]invoice[]] PASSED*",
            "*::TestPure::test_map_is_pure[[]invoice[]] PASSED*",
        ]
    )

    _recording(folder, "reminder", "Reminder")
    second, more = _outcomes(pytester, path)
    assert more.get("failed", 0) == 0, second.stdout.str()
    assert more["passed"] == counts["passed"] + 2
    second.stdout.fnmatch_lines_random(
        ["*::TestPure::test_map_matches_expected[[]reminder[]] PASSED*"]
    )

    _recording(folder, "wrong", "Receipt", expected_subject="Not what map returns")
    third, last = _outcomes(pytester, path)
    assert last["passed"] == more["passed"] + 1
    assert last["failed"] == 1
    third.stdout.fnmatch_lines(["FAILED *::TestPure::test_map_matches_expected[[]wrong[]]*"])
    assert contracts.contract_impls()["integrations.connector.demo"] == {"fake"}


@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P0-12")
def test_impure_map_fails_contract(pytester: pytest.Pytester, contracts: Any) -> None:
    """T-P0-12-08
    A demo connector whose map reads datetime.now() fails test_map_is_pure; one whose map
    opens a socket fails it with SocketBlockedError; the pure demo passes it.
    """
    classes = "\n".join(
        CLASS.format(impl=impl) for impl in ("Pure", "ReadsTheClock", "OpensASocket")
    )
    path = pytester.makepyfile(test_demo_contract=DEMO.replace("{classes}", classes))
    _recording(pytester.path / "recordings" / "demo", "invoice", "Invoice")

    result, _ = _outcomes(pytester, path)
    result.stdout.fnmatch_lines_random(
        [
            "*::TestPure::test_map_is_pure[[]invoice[]] PASSED*",
            "FAILED *::TestReadsTheClock::test_map_is_pure[[]invoice[]]*",
            "FAILED *::TestOpensASocket::test_map_is_pure[[]invoice[]]*SocketBlockedError*",
        ]
    )
