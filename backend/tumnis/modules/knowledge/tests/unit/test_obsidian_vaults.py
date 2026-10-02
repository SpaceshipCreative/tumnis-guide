"""Obsidian vault connections, the pure parts (P3-12, FR-15.10): the deploy key made for a
Git remote, the vault a watched change belongs to, and how a stored vault reads back."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import asyncssh
import pytest

from tumnis.modules.knowledge.adapters.sftp import fingerprint
from tumnis.modules.knowledge.obsidian import vaults

REPO = Path(__file__).resolve().parents[6]
CONNECTION = UUID("0190a7a0-0000-7000-8000-0000000000c2")
PROJECT = UUID("0190a7a0-0000-7000-8000-0000000000a1")


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
def test_deploy_key_is_a_fresh_ed25519_pair() -> None:
    """The private half opens as an ed25519 key whose public half is the line shown (one
    authorized_keys line, no newline); two vaults never share a key."""
    private, public = vaults.new_deploy_key()
    key = asyncssh.import_private_key(private)
    assert key.algorithm == b"ssh-ed25519"
    assert public == key.export_public_key("openssh").decode().strip()
    assert public.startswith("ssh-ed25519 ")
    assert public.endswith(vaults.KEY_COMMENT)
    assert "\n" not in public
    assert vaults.new_deploy_key()[1] != public


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
def test_a_watched_change_names_its_vault() -> None:
    roots = [("ws1", "c1", "/vaults/notes"), ("ws2", "c2", "/vaults/work/")]
    assert vaults.watched_vault("/vaults/notes/Inbox/Rates.md", roots) == ("ws1", "c1")
    assert vaults.watched_vault("/vaults/work/a.md", roots) == ("ws2", "c2")
    assert vaults.watched_vault("/vaults/notes-old/a.md", roots) is None
    assert vaults.watched_vault("/vaults/notes", roots) is None


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
def test_stored_vault_reads_back_with_its_host_key_fingerprint() -> None:
    host = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode()
    openssh = " ".join(host.split()[:2])
    out = vaults._out(  # the row shape the routes answer
        {
            "connection_id": CONNECTION,
            "mode": "git",
            "folder_path": None,
            "remote": "ssh://git@vault.example.com/notes.git",
            "branch": "main",
            "mapping": {
                "folders": [{"folder": "Clients/Acme", "project_id": str(PROJECT)}],
                "unmapped": "ignore",
                "clippings_folder": "Web",
                "extra_excludes": ["Private"],
            },
            "known_hosts": f"vault.example.com {openssh}",
            "deploy_public_key": "ssh-ed25519 AAAA tumnis-obsidian",
            "status": "ok",
            "last_error": None,
            "last_sync_at": None,
            "version": 3,
        }
    )
    assert out.id == CONNECTION
    assert out.host_key_sha256 == fingerprint(openssh)
    assert out.folders == [vaults.FolderRule(folder="Clients/Acme", project_id=PROJECT)]
    assert (out.unmapped, out.clippings_folder, out.extra_excludes) == (
        "ignore",
        "Web",
        ["Private"],
    )


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
def test_pruning_clones_keeps_the_ssh_key_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pruning removes only the clone of a vault that is gone. The `.keys` folder that holds
    a running command's deploy key and known_hosts, and anything else that is not a clone
    (the worker's HOME files), stay (CodeRabbit on #174)."""
    monkeypatch.setattr(vaults, "data_dir", lambda: tmp_path)
    kept, gone = str(CONNECTION), "0190a7a0-0000-7000-8000-0000000000c3"
    for name in (kept, gone, ".keys", ".ssh"):
        (tmp_path / name).mkdir()
    (tmp_path / ".keys" / "live.key").write_bytes(b"key")

    vaults._prune_clones({kept})

    assert sorted(child.name for child in tmp_path.iterdir()) == [".keys", ".ssh", kept]
    assert (tmp_path / ".keys" / "live.key").read_bytes() == b"key"


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
def test_vault_preview_schema_has_its_own_name() -> None:
    """The preview answer is `VaultPreviewOut` in the OpenAPI document, so the generated
    client needs no module-qualified name to tell it from Coolify's `PreviewOut`
    (CodeRabbit on #174). The committed document is the one `make gen` writes."""
    schemas = json.loads((REPO / "schemas" / "openapi.json").read_text())["components"]["schemas"]
    assert "VaultPreviewOut" in schemas
    assert "PreviewOut" in schemas  # Coolify's, unqualified now that nothing clashes
    assert not [name for name in schemas if name.endswith("__PreviewOut")]
