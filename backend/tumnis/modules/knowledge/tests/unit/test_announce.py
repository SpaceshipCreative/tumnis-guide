"""A knowledge write's live message (P1-17 review follow-up, R-05): a project's item
announces its project; a workspace knowledge base item (no project) announces itself as
`knowledge`, so workspace lists, search and quota refresh as well."""

from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from tumnis.core import live
from tumnis.modules.knowledge.api import announce

PROJECT = UUID("0192f3a4-1111-7000-8000-000000000001")
DOC = UUID("0192f3a4-2222-7000-8000-000000000002")


def _marks(session: Session) -> set[tuple[str, str]]:
    marks: set[tuple[str, str]] = session.info.get(live._MARKS, set())
    return marks


@pytest.mark.wp("P1-17")
@pytest.mark.req("FR-15.1")
def test_a_project_item_announces_its_project() -> None:
    session = Session()
    announce(session, PROJECT, DOC)
    assert _marks(session) == {("project", str(PROJECT))}


@pytest.mark.wp("P1-17")
@pytest.mark.req("FR-15.1")
def test_a_workspace_item_announces_knowledge() -> None:
    session = Session()
    announce(session, None, DOC)
    assert _marks(session) == {("knowledge", str(DOC))}
