"""Search matching and ranking (P0-20, FR-3.9, PERF-1): prefixes, phrases, the fixture
corpus's expected orders (text match, recency, project match), workspace isolation and
stable cursor pages. Rows are written straight into `search_index` (TDD step 2)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.search.tests.integration import _search

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext
    from tumnis.modules.search.api import SearchHit

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _search_ids(
    ctx: WorkspaceContext, q: str, clock: FixedClock, **kw: object
) -> list[uuid.UUID]:
    return [hit.entity_id for hit in await _run(ctx, q, clock, **kw)]


async def _run(ctx: WorkspaceContext, q: str, clock: FixedClock, **kw: object) -> list[SearchHit]:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.search import api  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        page = await api.search(s, q, now=clock.now(), **kw)  # type: ignore[arg-type]
    return page.items


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
async def test_prefix_inv_finds_invoice(
    search_db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-20-05
    `inv` (no trailing space: a prefix) finds "Send invoice to Acme" and "Invoices for
    March", not "Send quote to Acme".
    """
    at = clock.now()
    invoice = await _search.insert_doc(workspace.ctx, "Send invoice to Acme", at)
    plural = await _search.insert_doc(workspace.ctx, "Invoices for March", at)
    await _search.insert_doc(workspace.ctx, "Send quote to Acme", at)

    assert set(await _search_ids(workspace.ctx, "inv", clock)) == {invoice, plural}


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
async def test_prefix_survives_stemming(
    search_db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-20-06
    `runn` finds "running the tests", though english stems "running" to `run`: the simple
    lexemes keep "running" whole. The complete word `tests ` (stemmed) finds it too.
    """
    doc = await _search.insert_doc(workspace.ctx, "running the tests", clock.now())

    assert await _search_ids(workspace.ctx, "runn", clock) == [doc]
    assert await _search_ids(workspace.ctx, "tests ", clock) == [doc]


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
async def test_phrase_query(
    search_db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-20-07
    `"send invoice"` matches "Send invoice to Acme" and not "Invoice: send by Friday"
    (same words, other order); both match the plain terms `send invoice `.
    """
    at = clock.now()
    ordered = await _search.insert_doc(workspace.ctx, "Send invoice to Acme", at)
    reversed_ = await _search.insert_doc(workspace.ctx, "Invoice: send by Friday", at)

    assert await _search_ids(workspace.ctx, '"send invoice"', clock) == [ordered]
    assert set(await _search_ids(workspace.ctx, "send invoice ", clock)) == {ordered, reversed_}


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
async def test_fixture_corpus_order(
    search_db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-20-08
    The corpus (backend/fixtures/search/corpus.yaml: 12 rows, 5 queries): each query,
    with its project boost when it names one, returns exactly its expected keys in order.
    The first: identical "Invoice" rows, project_id=Acme -> Acme 1 d, Beta 1 d, Acme 60 d,
    Beta 60 d.
    """
    corpus = _search.load_corpus()
    keys = await _search.corpus_rows(workspace.ctx, corpus, clock.now())
    assert len(keys) == 12
    assert len(corpus["queries"]) == 5

    for query in corpus["queries"]:
        project_id = _search.corpus_project(corpus, query.get("project"))
        got = await _search_ids(workspace.ctx, query["q"], clock, project_id=project_id)
        assert [keys[entity_id] for entity_id in got] == query["expected"], query["q"]


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
async def test_results_are_workspace_isolated(
    search_db: DbUrls,
    two_workspaces: tuple[WorkspaceHandle, WorkspaceHandle],
    clock: FixedClock,
) -> None:
    """T-P0-20-09
    "Invoice Acme" is indexed in A and in B. Searching `invoice` in B's context returns
    only B's row; in A's, only A's; the typeaheads likewise.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.search import api  # noqa: PLC0415

    a, b = two_workspaces
    in_a = await _search.insert_doc(a.ctx, "Invoice Acme", clock.now())
    in_b = await _search.insert_doc(b.ctx, "Invoice Acme", clock.now())
    project_b = await _search.insert_doc(
        b.ctx, "Invoice project", clock.now(), entity_type="project"
    )

    assert set(await _search_ids(b.ctx, "invoice", clock)) == {in_b, project_b}
    assert await _search_ids(a.ctx, "invoice", clock) == [in_a]
    async with tenant_session(a.ctx) as s:
        assert await api.typeahead_projects(s, "inv", now=clock.now()) == []
        assert [
            h.entity_id for h in await api.typeahead_tasks(s, "inv", None, now=clock.now())
        ] == [in_a]
    async with tenant_session(b.ctx) as s:
        assert [h.entity_id for h in await api.typeahead_projects(s, "inv", now=clock.now())] == [
            project_b
        ]


@pytest.mark.req("FR-3.9", "PERF-1")
@pytest.mark.wp("P0-20")
async def test_cursor_pages_are_stable(
    search_db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-20-10
    23 matching rows, several sharing a score (same title and age: the tie breaks on
    entity_id). Walking pages of 4 returns every row exactly once, in score order, even
    though a newer matching row is inserted and the clock moves 30 days between pages
    (`now` is frozen in the cursor). A tampered cursor is 400 `invalid_cursor`.
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.search import api  # noqa: PLC0415

    start = clock.now()
    rows: set[uuid.UUID] = set()
    for i in range(23):
        title = "Invoice batch" if i % 3 else f"Invoice batch invoice {i}"
        rows.add(await _search.insert_doc(workspace.ctx, title, start - timedelta(days=i % 5)))

    seen: list[SearchHit] = []
    cursor: str | None = None
    for turn in range(20):
        async with tenant_session(workspace.ctx) as s:
            page = await api.search(s, "invoice", cursor=cursor, limit=4, now=clock.now())
        seen.extend(page.items)
        if turn == 0:
            # Ranks above every row walked so far (three matches, age 0): it lands before
            # the cursor, so the walk must neither repeat nor skip a row.
            await _search.insert_doc(workspace.ctx, "Invoice invoice invoice", clock.now())
            clock.advance(timedelta(days=30))
        cursor = page.next_cursor
        if cursor is None:
            break

    ids = [hit.entity_id for hit in seen]
    assert len(ids) == len(set(ids))
    assert set(ids) == rows
    order = [(-hit.score, hit.entity_id) for hit in seen]
    assert order == sorted(order)

    async with tenant_session(workspace.ctx) as s:
        with pytest.raises(ProblemError) as refused:
            await api.search(s, "invoice", cursor="not-a-cursor", now=clock.now())
    assert (refused.value.problem.status, refused.value.problem.code) == (400, "invalid_cursor")
