"""The virus scanner's contract (P1-16, SEC-10): `ClamAV` speaks clamd's INSTREAM protocol
(`zINSTREAM\\0`, chunks framed with a 4-byte big-endian length, four zero bytes to end;
the reply is `stream: OK` or `stream: <signature> FOUND`), checked against the `clamd`
container; `FakeClamAV` flags the EICAR test file only (A6) and passes the same cases."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.modules.knowledge.adapters.port import Scanner
from tumnis.modules.knowledge.tests._samples import eicar, stream

if TYPE_CHECKING:
    from tests._services import ClamdEndpoint
    from tests.fixtures import Fakes
    from tumnis.core.clock import FixedClock


class ScannerContract(AdapterContract[Scanner]):
    port, adapter_name = Scanner, "knowledge.clamav"

    async def test_clean_text_is_ok(self, subject: Scanner) -> None:
        result = await subject.scan(stream(b"Plain notes about the Acme redesign.\n"))
        assert result.infected is False
        assert result.signature is None

    async def test_eicar_is_found(self, subject: Scanner) -> None:
        result = await subject.scan(stream(eicar(), size=16))  # split over several frames
        assert result.infected is True
        assert result.signature is not None
        assert "eicar" in result.signature.lower()

    async def test_eicar_inside_a_larger_file_is_found(self, subject: Scanner) -> None:
        body = b"x" * 70_000 + eicar()  # the test string past the first frame
        result = await subject.scan(stream(body))
        assert result.infected is True

    async def test_large_clean_stream_in_many_frames(self, subject: Scanner) -> None:
        body = bytes(range(256)) * 12_288  # 3 MiB
        result = await subject.scan(stream(body))
        assert result.infected is False

    async def test_empty_stream_is_ok(self, subject: Scanner) -> None:
        result = await subject.scan(stream(b""))
        assert result.infected is False


@pytest.mark.contract
@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
class TestFakeClamAV(ScannerContract):
    """T-P1-16-05
    The fake scanner flags EICAR and nothing else.
    """

    impl = "fake"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> Scanner:
        scanner: Scanner = fakes["knowledge.clamav"]
        return scanner


@pytest.mark.contract
@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
class TestClamAV(ScannerContract):
    """T-P1-16-05
    INSTREAM framing against the `clamd` container: clean text is OK, EICAR is found.
    """

    impl = "real"

    @pytest.fixture
    def subject(self, clamd: ClamdEndpoint, clock: FixedClock) -> Scanner:
        from tumnis.modules.knowledge.adapters.clamav import ClamAV  # noqa: PLC0415

        return ClamAV(clamd.host, clamd.port, clock=clock)
