"""FakeDocling's page image is a well-formed PNG (P1-16, FR-15.2): fakes obey the real
contract, and a real vision model would refuse a broken image."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from tumnis.modules.knowledge.adapters.fake import FakeDocling

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunks(png: bytes) -> list[tuple[bytes, bytes]]:
    """Every (type, data) chunk, checking each declared length and CRC (PNG spec 5.3)."""
    assert png[:8] == SIGNATURE
    found, at = [], 8
    while at < len(png):
        (length,) = struct.unpack(">I", png[at : at + 4])
        kind, data = png[at + 4 : at + 8], png[at + 8 : at + 8 + length]
        assert len(data) == length, kind
        (crc,) = struct.unpack(">I", png[at + 8 + length : at + 12 + length])
        assert crc == zlib.crc32(kind + data), kind
        found.append((kind, data))
        at += 12 + length
    return found


@pytest.mark.req("FR-15.2")
@pytest.mark.wp("P1-16")
def test_fake_page_image_is_a_valid_png() -> None:
    """Signature, IHDR first and IEND last, lengths and CRCs that match, and IDAT data
    that inflates to one filtered row per scanline of the declared size."""
    png = FakeDocling().page_image(Path("any.pdf"), 1)

    chunks = _chunks(png)

    kinds = [kind for kind, _ in chunks]
    assert kinds[0] == b"IHDR"
    assert kinds[-1] == b"IEND"
    width, height, depth, colour = struct.unpack(">IIBB", chunks[0][1][:10])
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(b"".join(data for kind, data in chunks if kind == b"IDAT"))
    assert len(raw) == height * (1 + width * channels * depth // 8)
