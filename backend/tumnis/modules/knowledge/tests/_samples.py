"""Test payloads for upload safety (P1-16). No assertions live here.

The EICAR test file is built at run time from two halves, never committed whole: virus
scanners on dev machines quarantine a committed copy (plan: `eicar.txt` is written at test
time from the standard string)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

EXTRACTION = Path(__file__).resolve().parents[4] / "fixtures" / "extraction"

_EICAR_HEAD = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$"
_EICAR_TAIL = "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def eicar() -> bytes:
    """The 68-byte EICAR anti-malware test string."""
    return (_EICAR_HEAD + _EICAR_TAIL).encode("ascii")


def fixture_bytes(name: str) -> bytes:
    """A file from backend/fixtures/extraction; `eicar.txt` is made here."""
    if name == "eicar.txt":
        return eicar()
    return (EXTRACTION / name).read_bytes()


async def stream(data: bytes, size: int = 64 * 1024) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]
