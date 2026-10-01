"""`passages_for(task)` (P1-17, FR-15.4): the project brief, then the knowledge passages a
task's title, acceptance criteria and project goal find, within the packet's cap."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests._samples import fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import rows, settled, upload

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

BRIEF = "Acme's marketing site rebuild. Quotes use the rate card's hourly rates."


@pytest.mark.req("FR-15.4", "FR-2.3")
@pytest.mark.wp("P1-17")
async def test_passages_for_task(
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    session_client: SessionClient,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-17-13
    For the A1.5 task (`Quote Acme for the redesign` in the project holding the extracted
    rate card), `passages_for` returns the project brief first (its document, no chunk,
    its whole text), then the rate card's table passage once, citing the document's title
    and page 2; the total stays within PASSAGE_CAP_CHARS.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.rules import PASSAGE_CAP_CHARS  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    env = extract_env
    accepted = await upload(
        session_client, env.project_id, "rate-card-table.pdf", fixture_bytes("rate-card-table.pdf")
    )
    assert accepted.status_code == 202, accepted.text
    doc = await settled(session_client, accepted.json()["id"])
    [table] = rows(
        db,
        "SELECT c.id FROM chunks c JOIN documents d ON d.current_version_id = c.document_version_id"
        " WHERE d.id = %s AND c.text LIKE '%%Senior designer%%'",
        doc["id"],
    )
    async with tenant_session(env.ws.ctx) as s:
        brief_id = await knowledge.put_text_document(
            s, env.project_id, title="Acme site brief", body_md=BRIEF, role="brief"
        )
        task = await tasks.create_task(
            s,
            env.ws.ctx.actor,
            tasks.TaskCreate(project_id=env.project_id, title="Quote Acme for the redesign"),
            now=clock.now(),
        )

    async with tenant_session(env.ws.ctx) as s:
        out = await knowledge.passages_for(s, task.id)

    assert out[0].document_id == brief_id
    assert out[0].chunk_id is None
    assert out[0].text == BRIEF
    tables = [p for p in out[1:] if p.chunk_id == table["id"]]
    assert len(tables) == 1
    assert tables[0].page == 2
    assert tables[0].title == doc["title"]
    assert str(tables[0].document_id) == doc["id"]
    assert sum(len(p.text) for p in out) <= PASSAGE_CAP_CHARS
