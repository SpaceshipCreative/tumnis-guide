"""Syncing an Obsidian vault from a mounted folder (P3-12, FR-15.10, FR-15.8): the fixture
vault becomes knowledge-base Documents mapped to projects, with its wikilinks and embeds
kept as `document_links`; edits, renames and deletes in the vault follow; synced notes are
read-only in Tumnis."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests.integration._vault import (
    copy_vault,
    document_links,
    vault_documents,
    vault_env,
    version_numbers,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractDirs

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

NOTES = {"Clients/Acme/Kickoff.md", "Inbox/Rates.md", "Ideas/Launch.md", "Clippings/Article.md"}
DIAGRAM = "Attachments/diagram.png"


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
async def test_fixture_vault_maps_to_projects_with_links(
    db: DbUrls,
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    extract_dirs: ExtractDirs,
    tmp_path: Path,
) -> None:
    """T-P3-12-07
    The whole fixture vault through `FolderReader`: four notes become Documents, mapped by
    folder (Kickoff to Acme), frontmatter (Rates to Acme), tag (Launch to Lab) and the
    unmapped handling (Article to the workspace knowledge base); templates, `.obsidian/`
    and `.trash/` are never read. Clippings are untrusted and tainted, other notes trusted.
    `document_links` hold Kickoff's link to Rates with heading `Pricing`, its link to
    Launch, its Markdown link to Rates and its embed of diagram.png; Launch's link to a
    missing note keeps its raw target with no document. The embedded PNG becomes a
    pending_scan file Document with one extraction requested, its bytes in the spool. A
    second sync with nothing changed writes nothing new.
    """
    _folder = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.folder")
    FolderReader = _folder.FolderReader  # noqa: N806
    vault_sync = importlib.import_module("tumnis.modules.knowledge.obsidian.sync")
    _rules = importlib.import_module("tumnis.modules.knowledge.obsidian.rules")
    VaultMapping = _rules.VaultMapping  # noqa: N806

    vault = copy_vault(tmp_path / "vault")
    env = await vault_env(knowledge_ws, clock)
    reader = FolderReader(vault, clock=clock)
    mapping = VaultMapping(folders={"Clients/Acme": "acme"})

    await vault_sync.sync_vault(
        knowledge_ws.ctx, env.connection_id, reader, mapping, extract=env.extract
    )

    docs = await vault_documents(env)
    assert set(docs) == {*NOTES, DIAGRAM}
    acme, lab = env.projects["acme"], env.projects["lab"]
    notes = {path: docs[path]["project_id"] for path in NOTES}
    assert notes == {
        "Clients/Acme/Kickoff.md": acme,
        "Inbox/Rates.md": acme,
        "Ideas/Launch.md": lab,
        "Clippings/Article.md": None,
    }
    kickoff, rates, launch = (
        docs[p] for p in ("Clients/Acme/Kickoff.md", "Inbox/Rates.md", "Ideas/Launch.md")
    )
    assert (kickoff["title"], kickoff["kind"], kickoff["status"]) == ("Kickoff", "text", "ready")
    assert set(kickoff["tags"]) == {"meeting", "client/acme", "followup"}
    assert "tags:" not in kickoff["body_md"]
    assert (kickoff["trust"], kickoff["tainted"]) == ("trusted", False)
    article = docs["Clippings/Article.md"]
    assert (article["trust"], article["tainted"]) == ("untrusted", True)

    diagram = docs[DIAGRAM]
    assert (diagram["kind"], diagram["status"], diagram["project_id"]) == (
        "file",
        "pending_scan",
        acme,
    )
    assert len(env.extractions) == 1
    workspace_id, version_id, path = env.extractions[0]
    assert (workspace_id, path) == (knowledge_ws.id, DIAGRAM)
    assert (extract_dirs.spool / str(version_id)).read_bytes() == (vault / DIAGRAM).read_bytes()

    links = await document_links(env)
    by_source = {
        (row["kind"], row["to_target"], row["heading"], row["to_document_id"])
        for row in links
        if row["from_document_id"] == kickoff["id"]
    }
    assert by_source == {
        ("link", "Rates", "Pricing", rates["id"]),
        ("link", "Ideas/Launch", None, launch["id"]),
        ("link", "Inbox/Rates.md", None, rates["id"]),
        ("embed", "diagram.png", None, diagram["id"]),
    }
    missing = [row for row in links if row["from_document_id"] == launch["id"]]
    assert [(r["to_target"], r["to_document_id"]) for r in missing] == [("Missing note", None)]

    await vault_sync.sync_vault(
        knowledge_ws.ctx, env.connection_id, reader, mapping, extract=env.extract
    )
    again = await vault_documents(env)
    assert {p: d["version"] for p, d in again.items()} == {p: d["version"] for p, d in docs.items()}
    assert await version_numbers(env, kickoff["id"]) == [1]
    assert len(env.extractions) == 1
    assert len(await document_links(env)) == len(links)


@pytest.mark.req("FR-15.8")
@pytest.mark.wp("P3-12")
async def test_edit_rename_delete_in_vault(
    db: DbUrls, knowledge_ws: WorkspaceHandle, clock: FixedClock, tmp_path: Path
) -> None:
    """T-P3-12-08
    After a first sync: an edit in the vault makes a new version of the note's Document; a
    rename (the same content hash gone from one path and new at another in one scan) keeps
    the Document, with the new path and title and no new version; a delete sends the
    Document to the trash. A note that comes back at its old path is restored.
    """
    _folder = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.folder")
    FolderReader = _folder.FolderReader  # noqa: N806
    vault_sync = importlib.import_module("tumnis.modules.knowledge.obsidian.sync")
    _rules = importlib.import_module("tumnis.modules.knowledge.obsidian.rules")
    VaultMapping = _rules.VaultMapping  # noqa: N806

    vault = copy_vault(tmp_path / "vault")
    env = await vault_env(knowledge_ws, clock)
    reader = FolderReader(vault, clock=clock)
    mapping = VaultMapping(folders={"Clients/Acme": "acme"})

    async def sync() -> None:
        await vault_sync.sync_vault(
            knowledge_ws.ctx, env.connection_id, reader, mapping, extract=env.extract
        )

    await sync()
    before = await vault_documents(env)

    rates = vault / "Inbox/Rates.md"
    rates.write_text(rates.read_text() + "\nJunior designer: 90 per hour.\n")
    (vault / "Ideas/Launch.md").rename(vault / "Ideas/Launch plan.md")
    article_text = (vault / "Clippings/Article.md").read_text()
    (vault / "Clippings/Article.md").unlink()
    await sync()

    after = await vault_documents(env)
    edited = after["Inbox/Rates.md"]
    assert edited["id"] == before["Inbox/Rates.md"]["id"]
    assert await version_numbers(env, edited["id"]) == [1, 2]
    assert "Junior designer" in edited["body_md"]

    assert "Ideas/Launch.md" not in after
    renamed = after["Ideas/Launch plan.md"]
    assert renamed["id"] == before["Ideas/Launch.md"]["id"]
    assert renamed["title"] == "Launch plan"
    assert await version_numbers(env, renamed["id"]) == [1]

    assert "Clippings/Article.md" not in after
    trashed = await vault_documents(env, trashed=True)
    assert trashed["Clippings/Article.md"]["id"] == before["Clippings/Article.md"]["id"]

    (vault / "Clippings/Article.md").write_text(article_text)
    await sync()
    back = await vault_documents(env)
    assert back["Clippings/Article.md"]["id"] == before["Clippings/Article.md"]["id"]


@pytest.mark.req("FR-15.8")
@pytest.mark.wp("P3-12")
async def test_synced_notes_read_only(
    db: DbUrls,
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    session_client: SessionClient,
) -> None:
    """T-P3-12-12
    A vault Document cannot be changed in Tumnis: PATCH on it (a body, a title, tags or a
    pin) answers 409 `read_only_source`, and the Document is unchanged. The vault is the
    source; the user edits the note in Obsidian.
    """
    _folder = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.folder")
    FolderReader = _folder.FolderReader  # noqa: N806
    vault_sync = importlib.import_module("tumnis.modules.knowledge.obsidian.sync")
    _rules = importlib.import_module("tumnis.modules.knowledge.obsidian.rules")
    VaultMapping = _rules.VaultMapping  # noqa: N806

    vault = copy_vault(tmp_path / "vault")
    env = await vault_env(knowledge_ws, clock)
    await vault_sync.sync_vault(
        knowledge_ws.ctx,
        env.connection_id,
        FolderReader(vault, clock=clock),
        VaultMapping(folders={"Clients/Acme": "acme"}),
        extract=env.extract,
    )
    kickoff = (await vault_documents(env))["Clients/Acme/Kickoff.md"]
    url = f"/v1/knowledge/documents/{kickoff['id']}"

    for patch in (
        {"body_md": "Overwritten."},
        {"title": "Renamed"},
        {"tags": ["x"]},
        {"pinned": True},
    ):
        answer = await session_client.patch(url, json={**patch, "version": kickoff["version"]})
        assert answer.status_code == 409, answer.text
        assert answer.json()["code"] == "read_only_source"

    unchanged = (await vault_documents(env))["Clients/Acme/Kickoff.md"]
    assert unchanged["version"] == kickoff["version"]
    assert unchanged["body_md"] == kickoff["body_md"]


@pytest.mark.req("FR-15.8")
@pytest.mark.wp("P3-12")
async def test_synced_notes_cannot_be_deleted_in_tumnis(
    db: DbUrls,
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    session_client: SessionClient,
) -> None:
    """A vault Document is not deleted in Tumnis either: DELETE on it, and the
    delete-at-source dialog's confirmation, answer 409 `read_only_source` before P3-14's
    outcomes (decision 81), and the Document stays live. The note is deleted in the vault;
    the next sync trashes it."""
    _folder = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.folder")
    FolderReader = _folder.FolderReader  # noqa: N806
    vault_sync = importlib.import_module("tumnis.modules.knowledge.obsidian.sync")
    _rules = importlib.import_module("tumnis.modules.knowledge.obsidian.rules")
    VaultMapping = _rules.VaultMapping  # noqa: N806

    vault = copy_vault(tmp_path / "vault")
    env = await vault_env(knowledge_ws, clock)
    await vault_sync.sync_vault(
        knowledge_ws.ctx,
        env.connection_id,
        FolderReader(vault, clock=clock),
        VaultMapping(folders={"Clients/Acme": "acme"}),
        extract=env.extract,
    )
    kickoff = (await vault_documents(env))["Clients/Acme/Kickoff.md"]
    url = f"/v1/knowledge/documents/{kickoff['id']}"

    for answer in (
        await session_client.delete(url),
        await session_client.post(f"{url}/delete-confirmation"),
    ):
        assert answer.status_code == 409, answer.text
        assert answer.json()["code"] == "read_only_source"

    assert "Clients/Acme/Kickoff.md" in await vault_documents(env)


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
async def test_lost_attachment_extraction_is_requested_again(
    db: DbUrls,
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    extract_dirs: ExtractDirs,
    tmp_path: Path,
) -> None:
    """An embedded attachment whose extraction request was lost (the scan committed, the
    enqueue failed) stays `pending_scan` with an unchanged file: a scan an hour later
    requests the same version again, spooling its bytes again when they are gone. A scan
    within the hour does not (as T-P3-12-07's second sync)."""
    _folder = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.folder")
    FolderReader = _folder.FolderReader  # noqa: N806
    vault_sync = importlib.import_module("tumnis.modules.knowledge.obsidian.sync")
    _rules = importlib.import_module("tumnis.modules.knowledge.obsidian.rules")
    VaultMapping = _rules.VaultMapping  # noqa: N806

    vault = copy_vault(tmp_path / "vault")
    env = await vault_env(knowledge_ws, clock)
    reader = FolderReader(vault, clock=clock)
    mapping = VaultMapping(folders={"Clients/Acme": "acme"})
    lost: list[object] = []

    async def lose(_workspace_id: object, version_id: object, _path: object) -> None:
        lost.append(version_id)
        raise RuntimeError("the enqueue failed")

    async def sync(extract: object) -> None:
        await vault_sync.sync_vault(
            knowledge_ws.ctx, env.connection_id, reader, mapping, extract=extract
        )

    with pytest.raises(RuntimeError, match="the enqueue failed"):
        await sync(lose)
    diagram = (await vault_documents(env))[DIAGRAM]
    assert diagram["status"] == "pending_scan"
    assert len(lost) == 1
    version_id = lost[0]
    spooled = extract_dirs.spool / str(version_id)
    spooled.unlink()

    await sync(env.extract)
    assert env.extractions == []

    clock.advance(hours=1)
    await sync(env.extract)
    assert env.extractions == [(knowledge_ws.id, version_id, DIAGRAM)]
    assert spooled.read_bytes() == (vault / DIAGRAM).read_bytes()
    assert await version_numbers(env, diagram["id"]) == [1]
