"""The chunk token limit must hold some text: a zero or negative limit is refused before
Docling or a tokenizer loads, so a bad setting fails loudly instead of storing no chunks."""

import pytest

from tumnis.modules.knowledge import extraction
from tumnis.modules.knowledge.adapters.docling import DoclingExtractor

pytestmark = [pytest.mark.req("ADR-0007"), pytest.mark.wp("P1-16")]


@pytest.mark.parametrize("max_tokens", [0, -1])
def test_make_chunker_refuses_a_non_positive_limit(max_tokens: int) -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        extraction.make_chunker("any/tokenizer", max_tokens)


@pytest.mark.parametrize("max_tokens", [0, -512])
def test_extractor_refuses_a_non_positive_limit(max_tokens: int) -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        DoclingExtractor(chunk_tokenizer="any/tokenizer", max_tokens=max_tokens)
