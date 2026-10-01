"""The vector query's SQL (P3-10): the model and the dimension are literals that match the
model's partial HNSW index, and anything that is not a model name Tumnis accepts never
reaches the SQL."""

from __future__ import annotations

from uuid import UUID

import pytest

from tumnis.modules.knowledge.rules import CANDIDATES, hnsw_index_name
from tumnis.modules.knowledge.search import vector_query

PROJECT = UUID("01900000-0000-7000-8000-000000000001")
OTHER = UUID("01900000-0000-7000-8000-000000000002")


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
def test_vector_query_matches_the_partial_index_and_binds_only_the_vector_and_scope() -> None:
    sql, params = vector_query(
        "BAAI/bge-m3", 3, [1.0, 0.0, 0.0], project_id=PROJECT, project_ids=None, candidates=50
    )

    assert "e.model = 'BAAI/bge-m3'" in sql
    assert sql.count("(e.embedding::vector(3)) <=> CAST(:q AS vector)") == 2
    assert "(e.project_id = :project_id OR e.project_id IS NULL)" in sql
    assert params == {"q": "[1.0,0.0,0.0]", "candidates": 50, "project_id": PROJECT}
    assert hnsw_index_name("BAAI/bge-m3") == "embeddings_hnsw_baai_bge_m3"


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
def test_vector_query_scopes() -> None:
    limited, params = vector_query(
        "m", 2, [0.6, 0.8], project_id=None, project_ids=frozenset({OTHER}), candidates=CANDIDATES
    )
    everything, bare = vector_query(
        "m", 2, [0.6, 0.8], project_id=None, project_ids=None, candidates=CANDIDATES
    )

    assert "ANY(:project_ids)" in limited
    assert params["project_ids"] == [OTHER]
    assert "project_id" not in everything
    assert set(bare) == {"q", "candidates"}


@pytest.mark.req("FR-15.3")
@pytest.mark.wp("P3-10")
@pytest.mark.parametrize(
    ("model", "dims"),
    [("x' OR '1'='1", 3), ("", 3), ("ok-model", 0), ("ok-model", 2001), ("a b", 3)],
)
def test_vector_query_refuses_what_it_cannot_write_as_a_literal(model: str, dims: int) -> None:
    with pytest.raises(ValueError, match=r"dimension|model"):
        vector_query(model, dims, [0.0], project_id=None, project_ids=None, candidates=1)
