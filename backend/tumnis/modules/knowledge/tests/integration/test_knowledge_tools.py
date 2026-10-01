"""The agents' knowledge tools (P2-17, FR-15.4): `add_document`, `search_knowledge` and
`get_document`, each an MCP tool with its REST twin (R-36), within the caller's project
scope (R-28), and citations of a document and page in a run's result.

The tools' parity, scope and write rules on both doors are swept by the P2-01 meta-tests;
these tests prove what only the knowledge tools do."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.knowledge.tests.integration._upload import rows

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import KeyClientFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

AGENT_SCOPES = ["context:read", "knowledge:write"]


async def _note(http: Any, project_id: object | None, title: str, body_md: str) -> dict[str, Any]:
    body: dict[str, Any] = {"title": title, "body_md": body_md}
    if project_id is not None:
        body["project_id"] = str(project_id)
    made = await http.post("/v1/knowledge/documents/text", json=body)
    assert made.status_code == 201, made.text
    doc: dict[str, Any] = made.json()
    return doc


@pytest.mark.req("FR-15.4")
@pytest.mark.wp("P2-17")
async def test_add_document_saves_to_agent_outputs_untrusted(
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    key_client: KeyClientFactory,
    db: DbUrls,
) -> None:
    """T-P2-17-04
    `add_document` through its REST twin (`POST /v1/knowledge/documents/text`) by an
    agent's credential saves the Markdown as `agent-outputs/<name>.md` in the project's
    folder, as a document that is untrusted, written by the agent (`source` "agent",
    labeled `agent`), with its tags, its first version and its text searchable at once.
    """
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.storage import spool  # noqa: PLC0415

    env = extract_env
    agent = await key_client(AGENT_SCOPES, projects=[env.project_id])
    made = await agent.post(
        "/v1/knowledge/documents/text",
        json={
            "project_id": str(env.project_id),
            "title": "Competitor pricing summary",
            "body_md": "# Pricing\n\nThe cheapest plan is 19 a month.",
            "tags": ["research", "pricing"],
        },
    )
    assert made.status_code == 201, made.text
    doc = made.json()
    assert (doc["trust"], doc["label"], doc["source"]) == ("untrusted", "agent", "agent")
    assert doc["tags"] == ["research", "pricing"]
    assert doc["status"] == "ready"
    assert doc["body_md"] == "# Pricing\n\nThe cheapest plan is 19 a month."

    files = rows(
        db,
        "SELECT path FROM folder_files WHERE document_id = %s AND deleted_at IS NULL",
        doc["id"],
    )
    assert len(files) == 1
    path = files[0]["path"]
    assert path.startswith(f"{env.folder}/agent-outputs/"), path
    assert path.endswith(".md"), path
    async with (
        tenant_session(env.ws.ctx) as s,
        knowledge.open_backend(s, env.location_id, net=NetPolicy(mode="self-hosted")) as backend,
    ):
        written = await spool(backend.read(path), limit=1_000_000)
    assert b"The cheapest plan is 19 a month." in written

    versions = rows(
        db, "SELECT version_no FROM document_versions WHERE document_id = %s", doc["id"]
    )
    assert [v["version_no"] for v in versions] == [1]
    found = await agent.get(
        "/v1/knowledge/search", params={"q": "cheapest plan", "project_id": str(env.project_id)}
    )
    assert found.status_code == 200, found.text
    assert doc["id"] in {hit["document_id"] for hit in found.json()["items"]}


@pytest.mark.req("FR-15.4")
@pytest.mark.wp("P2-17")
async def test_search_and_get_respect_project_scope(
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    session_client: SessionClient,
    key_client: KeyClientFactory,
    clock: FixedClock,
) -> None:
    """T-P2-17-05
    A credential limited to project A (as a task token is) finds A's documents and the
    workspace knowledge base's with `search_knowledge` (`GET /v1/knowledge/search`, a page
    of hits), never B's; naming B is 404 `not_found`. `get_document`
    (`GET /v1/knowledge/documents/{id}`) reads A's document and the workspace knowledge
    base's, and answers 404 `not_found` for B's, so B's existence never leaks (R-28)."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    env = extract_env
    async with tenant_session(env.ws.ctx) as s:
        beta = await projects.create_project(
            s, env.ws.ctx.actor, projects.ProjectCreate(name="Beta app"), now=clock.now()
        )
    ours = await _note(session_client, env.project_id, "Acme palette", "Teal palette for Acme.")
    theirs = await _note(session_client, beta.id, "Beta palette", "Orange palette for Beta.")
    shared = await _note(session_client, None, "House palette", "Grey palette for everyone.")

    agent = await key_client(["context:read"], projects=[env.project_id])
    found = await agent.get("/v1/knowledge/search", params={"q": "palette"})
    assert found.status_code == 200, found.text
    page = found.json()
    assert set(page) >= {"items", "next_cursor"}
    ids = {hit["document_id"] for hit in page["items"]}
    assert ours["id"] in ids
    assert shared["id"] in ids
    assert theirs["id"] not in ids

    scoped = await agent.get(
        "/v1/knowledge/search", params={"q": "palette", "project_id": str(env.project_id)}
    )
    assert scoped.status_code == 200, scoped.text
    assert {h["document_id"] for h in scoped.json()["items"]} == {ours["id"], shared["id"]}

    other = await agent.get(
        "/v1/knowledge/search", params={"q": "palette", "project_id": str(beta.id)}
    )
    assert other.status_code == 404, other.text
    assert other.json()["code"] == "not_found"

    own = await agent.get(f"/v1/knowledge/documents/{ours['id']}")
    assert own.status_code == 200, own.text
    assert own.json()["body_md"] == "Teal palette for Acme."
    house = await agent.get(f"/v1/knowledge/documents/{shared['id']}")
    assert house.status_code == 200, house.text
    assert house.json()["title"] == "House palette"
    hidden = await agent.get(f"/v1/knowledge/documents/{theirs['id']}")
    assert hidden.status_code == 404, hidden.text
    assert hidden.json()["code"] == "not_found"


@pytest.mark.req("FR-15.4")
@pytest.mark.wp("P2-17")
async def test_agent_cites_document_and_page(
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-17-06
    A run's result may link a document of its project (or of the workspace knowledge
    base) as `tumnis://doc/<id>#page=<n>` (kind `document`): the result keeps the link and
    labels it as a citation ("Brand guide, page 4"), on the task's result and on its
    `result` review item. A citation of another project's document is refused (422
    `invalid_citation`), and a `tumnis://` URL is only a document link."""
    from pydantic import ValidationError  # noqa: PLC0415

    from tests._mcp import make_world, running_run  # noqa: PLC0415
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    ws = knowledge_ws
    world = await make_world(ws, clock)
    async with tenant_session(ws.ctx) as s:
        guide = await knowledge.create_text_entry(
            s, world.projects["A"], "Brand guide", "# Type\n\nHeadings use Inter."
        )
        elsewhere = await knowledge.create_text_entry(
            s, world.projects["B"], "Beta rates", "Senior designer: 90 an hour."
        )
    cite = f"tumnis://doc/{guide.id}#page=4"

    run_id = await running_run(world, "A")
    async with tenant_session(ws.ctx) as s:
        result = await agents.accept_result(
            s,
            ws.ctx.actor,
            None,
            agents.PostResultIn(
                run_id=run_id,
                outcome="done",
                summary="Headings use Inter.",
                links=[tasks.ResultLink(kind="document", url=cite)],
            ),
            now=clock.now(),
        )
    [link] = result.links
    assert (link.kind, link.url, link.label) == ("document", cite, "Brand guide, page 4")
    [item] = rows(
        db,
        "SELECT payload FROM review_items WHERE kind = 'result' AND payload->>'run_id' = %s",
        str(run_id),
    )
    assert item["payload"]["links"] == [
        {"kind": "document", "url": cite, "label": "Brand guide, page 4"}
    ]

    other_run = await running_run(world, "A")
    with pytest.raises(ProblemError) as refused:
        async with tenant_session(ws.ctx) as s:
            await agents.accept_result(
                s,
                ws.ctx.actor,
                None,
                agents.PostResultIn(
                    run_id=other_run,
                    outcome="done",
                    summary="Rates from Beta.",
                    links=[
                        tasks.ResultLink(kind="document", url=f"tumnis://doc/{elsewhere.id}#page=1")
                    ],
                ),
                now=clock.now(),
            )
    assert refused.value.status == 422
    assert refused.value.code == "invalid_citation"

    with pytest.raises(ValidationError):
        tasks.ResultLink(kind="url", url=cite)
    with pytest.raises(ValidationError):
        tasks.ResultLink(kind="document", url="tumnis://doc/not-an-id#page=4")
