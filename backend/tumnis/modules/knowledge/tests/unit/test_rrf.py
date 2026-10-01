"""Reciprocal rank fusion and recall@k (P3-10, FR-15.3): the pure halves of hybrid search.

`rrf_merge` scores a chunk by the sum over the lists holding it of 1 / (k + rank), with
the constant k = 60 of the original RRF paper; `recall_at_k` is the eval set's measure.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st


def _ids(*names: str) -> dict[str, UUID]:
    return {name: UUID(int=n + 1) for n, name in enumerate(names)}


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
def test_rrf_merge_table() -> None:
    """T-P3-10-01
    Full text [a, b, c] and vector [c, a, d] with k = 60 fuse to a, c, b, d: a scores
    1/61 + 1/62, c 1/63 + 1/61, b 1/62 and d 1/63; items in one list only keep their one
    term, and each hit says which lists found it.
    """
    from tumnis.modules.knowledge.rules import (  # type: ignore[attr-defined]  # noqa: PLC0415
        RRF_K,
        Ranked,
        rrf_merge,
    )

    ids = _ids("a", "b", "c", "d")
    fulltext = [Ranked(ids["a"], 1), Ranked(ids["b"], 2), Ranked(ids["c"], 3)]
    vector = [Ranked(ids["c"], 1), Ranked(ids["a"], 2), Ranked(ids["d"], 3)]

    fused = rrf_merge(fulltext, vector)

    assert RRF_K == 60
    assert [hit.chunk_id for hit in fused] == [ids["a"], ids["c"], ids["b"], ids["d"]]
    scores = {hit.chunk_id: hit.score for hit in fused}
    assert scores[ids["a"]] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[ids["c"]] == pytest.approx(1 / 63 + 1 / 61)
    assert scores[ids["b"]] == pytest.approx(1 / 62)
    assert scores[ids["d"]] == pytest.approx(1 / 63)
    sources = {hit.chunk_id: set(hit.sources) for hit in fused}
    assert sources == {
        ids["a"]: {"fulltext", "vector"},
        ids["c"]: {"fulltext", "vector"},
        ids["b"]: {"fulltext"},
        ids["d"]: {"vector"},
    }
    assert [hit.chunk_id for hit in rrf_merge(fulltext, vector, limit=2)] == [ids["a"], ids["c"]]
    assert rrf_merge([], []) == []


_ranked_lists = st.lists(st.integers(min_value=0, max_value=30), unique=True, max_size=20)


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
@given(fulltext=_ranked_lists, vector=_ranked_lists)
def test_rrf_properties(fulltext: list[int], vector: list[int]) -> None:
    """T-P3-10-02
    For any two ranked lists: before the limit, the fused set is exactly the union of the
    inputs; an item ranked first in both lists ranks first; the same input gives the same
    output.
    """
    from tumnis.modules.knowledge.rules import (  # type: ignore[attr-defined]  # noqa: PLC0415
        Ranked,
        rrf_merge,
    )

    ft = [Ranked(UUID(int=n + 1), rank) for rank, n in enumerate(fulltext, start=1)]
    vec = [Ranked(UUID(int=n + 1), rank) for rank, n in enumerate(vector, start=1)]
    everything = len(ft) + len(vec) + 1

    fused = rrf_merge(ft, vec, limit=everything)

    assert {hit.chunk_id for hit in fused} == {r.chunk_id for r in [*ft, *vec]}
    assert len(fused) == len({hit.chunk_id for hit in fused})
    assert [hit.score for hit in fused] == sorted((hit.score for hit in fused), reverse=True)
    if ft and vec and ft[0].chunk_id == vec[0].chunk_id:
        assert fused[0].chunk_id == ft[0].chunk_id
    assert rrf_merge(ft, vec, limit=everything) == fused


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
@pytest.mark.xfail(strict=True, reason="spec:P3-10")
def test_recall_at_k() -> None:
    """T-P3-10-03
    Recall@k is the mean over queries of |expected ∩ top k| / min(k, |expected|): on a toy
    mapping, 1 of 2 found, 0 of 2 found and 1 of 1 found average to 0.5 at k = 5; at k = 1
    the first query's second expected item no longer counts against it.
    """
    from tumnis.modules.knowledge.rules import (  # type: ignore[attr-defined]  # noqa: PLC0415
        recall_at_k,
    )

    ids = _ids("a", "b", "c", "d", "e", "f", "g", "x", "y", "z")
    results = {
        "q1": [ids["a"], ids["b"], ids["c"], ids["d"], ids["e"], ids["f"]],
        "q2": [ids["x"]],
        "q3": [ids["g"]],
    }
    expected = {"q1": {ids["a"], ids["f"]}, "q2": {ids["y"], ids["z"]}, "q3": {ids["g"]}}

    assert recall_at_k(results, expected) == pytest.approx((0.5 + 0.0 + 1.0) / 3)
    assert recall_at_k(results, expected, k=1) == pytest.approx((1.0 + 0.0 + 1.0) / 3)
    assert recall_at_k({"q2": []}, {"q2": {ids["y"]}}) == 0.0
