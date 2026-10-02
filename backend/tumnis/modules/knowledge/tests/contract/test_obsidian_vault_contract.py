"""The Obsidian vault readers' contract (P3-12, FR-15.10): `FakeVault`, `FolderReader` over a
folder and `GitReader` over a clone of a local bare repository (real git, `file://`) list
and read the same vault the same way. Listings are sorted, carry sha256 etags, never show
`.git/`, and never open a folder `skip` excludes; reads refuse paths outside the vault."""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.modules.knowledge.adapters.obsidian.port import VaultReader
from tumnis.modules.knowledge.storage import NotFound, PathRejected
from tumnis.modules.knowledge.tests.integration._vault import git

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures import Fakes
    from tumnis.core.clock import FixedClock

VAULT: dict[str, bytes] = {
    "Inbox/Rates.md": b"# Rates\n\nNet 30 days.\n",
    "Clients/Acme/Kickoff.md": b"# Kickoff\n\nSee [[Rates]].\n",
    "Attachments/diagram.png": b"\x89PNG\r\n\x1a\nnot really a picture",
    "Private/Diary.md": b"# Diary\n\nNot for Tumnis.\n",
    "Welcome.md": b"# Welcome\n",
}
CONNECTION = UUID("0190a7a0-0000-7000-8000-0000000000c2")


def _write_vault(root: Path) -> None:
    for rel, data in VAULT.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _private(path: str) -> bool:
    return path == "Private" or path.startswith("Private/")


class VaultContract(AdapterContract[VaultReader]):
    port = VaultReader

    async def test_lists_every_file_sorted_with_sha256(self, subject: VaultReader) -> None:
        listed = await subject.list_files()
        assert [item.path for item in listed] == sorted(VAULT)
        for item in listed:
            assert item.size == len(VAULT[item.path])
            assert item.etag == hashlib.sha256(VAULT[item.path]).hexdigest()

    async def test_skip_prunes_before_reading(self, subject: VaultReader) -> None:
        asked: list[str] = []

        def skip(path: str) -> bool:
            asked.append(path)
            return _private(path)

        listed = await subject.list_files(skip)
        assert [item.path for item in listed] == sorted(p for p in VAULT if not _private(p))
        assert "Private" in asked
        assert "Private/Diary.md" not in asked  # the folder was pruned, never opened

    async def test_read_returns_the_bytes(self, subject: VaultReader) -> None:
        for rel, data in VAULT.items():
            assert await subject.read(rel) == data

    async def test_read_missing_is_not_found(self, subject: VaultReader) -> None:
        with pytest.raises(NotFound):
            await subject.read("Inbox/Gone.md")

    @pytest.mark.parametrize("path", ["../x", "a/../../x", "/etc/passwd", "a\x00b"])
    async def test_read_outside_the_vault_is_rejected(
        self, subject: VaultReader, path: str
    ) -> None:
        with pytest.raises(PathRejected):
            await subject.read(path)

    async def test_git_internals_are_not_vault_content(self, subject: VaultReader) -> None:
        listed = await subject.list_files()
        assert not any(item.path.split("/")[0] == ".git" for item in listed)
        with pytest.raises(PathRejected):
            await subject.read(".git/config")

    async def test_refresh_keeps_the_listing(self, subject: VaultReader) -> None:
        before = await subject.list_files()
        await subject.refresh()
        assert await subject.list_files() == before


@pytest.mark.contract
@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
class TestFakeVault(VaultContract):
    """T-P3-12-07
    The vault suite passes for `FakeVault`, as registered for the mounted folder.
    """

    impl = "fake"
    adapter_name = "knowledge.obsidian_folder"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> VaultReader:
        reader: VaultReader = fakes[self.adapter_name]
        for rel, data in VAULT.items():
            reader.script(rel, data)  # type: ignore[attr-defined]  # FakeVault
        reader.script(".git/config", b"[core]\n")  # type: ignore[attr-defined]
        return reader


class TestFakeVaultForGit(TestFakeVault):  # inherits the markers
    """T-P3-12-11
    The same fake, as registered for the Git path.
    """

    adapter_name = "knowledge.obsidian_git"


@pytest.mark.contract
@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
class TestFolderReader(VaultContract):
    """T-P3-12-07
    The vault suite passes for `FolderReader` on a temp folder (with a `.git/` folder that
    is never listed); a symlink is neither listed nor followed.
    """

    impl = "real"
    adapter_name = "knowledge.obsidian_folder"

    @pytest.fixture
    def subject(self, tmp_path: Path, clock: FixedClock) -> VaultReader:
        from tumnis.modules.knowledge.adapters.obsidian.folder import FolderReader  # noqa: PLC0415

        root = tmp_path / "vault"
        _write_vault(root)
        (root / ".git").mkdir()
        (root / ".git" / "config").write_bytes(b"[core]\n")
        return FolderReader(root, clock=clock)

    async def test_symlinks_are_not_followed(self, subject: VaultReader, tmp_path: Path) -> None:
        outside = tmp_path / "outside.md"
        outside.write_bytes(b"# Outside the vault\n")
        os.symlink(outside, tmp_path / "vault" / "Link.md")
        os.symlink(tmp_path, tmp_path / "vault" / "Loop")
        listed = await subject.list_files()
        assert [item.path for item in listed] == sorted(VAULT)
        with pytest.raises((NotFound, PathRejected)):
            await subject.read("Link.md")


@pytest.mark.contract
@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
class TestGitReader(VaultContract):
    """T-P3-12-11
    The vault suite passes for `GitReader` cloning a local bare repository over `file://`
    with the real git runner.
    """

    impl = "real"
    adapter_name = "knowledge.obsidian_git"

    @pytest.fixture
    async def subject(self, tmp_path: Path, clock: FixedClock) -> VaultReader:
        from tumnis.core.net import NetPolicy  # noqa: PLC0415
        from tumnis.modules.knowledge.adapters.obsidian.git import (  # noqa: PLC0415
            GitReader,
            SubprocessGitRunner,
        )

        bare = tmp_path / "remote.git"
        bare.mkdir()
        git("init", "--bare", "--initial-branch=main", cwd=bare)
        work = tmp_path / "work"
        _write_vault(work)
        git("init", "--initial-branch=main", cwd=work)
        git("add", "-A", cwd=work)
        git("commit", "-m", "vault", cwd=work)
        git("push", str(bare), "HEAD:refs/heads/main", cwd=work)
        reader = GitReader(
            connection_id=CONNECTION,
            remote=f"file://{bare}",
            branch="main",
            data_dir=tmp_path / "obsidian",
            deploy_key=None,
            known_hosts=None,
            runner=SubprocessGitRunner(),
            net=NetPolicy(mode="self-hosted"),
            clock=clock,
        )
        await reader.connect()
        return reader
