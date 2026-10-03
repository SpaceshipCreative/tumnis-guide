"""Docling's memory under worker-extract's limit (P1-16, ADR-0007, Scott decision 97).

Docling's threaded PDF pipeline reads pages ahead into the queues between its stages
(`queue_max_size`, 100 pages by default), so a long PDF holds hundreds of pages in memory at
once. Measured in the extract image under compose.yaml's 4 GiB `mem_limit`: 105 pages
peaked at 3.4 GiB and 210 pages were killed for memory; with 8 pages per queue, 105 pages
took 2.3 GiB and 210 pages 2.4 GiB, in the same time.

Needs Docling (`uv sync --group docling`): conftest.py's REAL_DOCLING_TESTS marks this test
`docling` and skips it where Docling is not installed.
"""

from __future__ import annotations

import pytest

from tumnis.modules.knowledge import extraction

MAX_PAGES_IN_FLIGHT = 8


@pytest.mark.req("FR-15.2", "ADR-0007")
@pytest.mark.wp("P1-16")
def test_converter_keeps_few_pages_in_flight() -> None:
    """The standard converter's PDF and image pipelines queue at most 8 pages between
    stages, so a long PDF converts in bounded memory."""
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415

    converter = extraction.standard_converter()
    for kind in (InputFormat.PDF, InputFormat.IMAGE):
        options = converter.format_to_options[kind].pipeline_options
        assert options is not None, kind
        assert getattr(options, "queue_max_size", None) == MAX_PAGES_IN_FLIGHT, kind
