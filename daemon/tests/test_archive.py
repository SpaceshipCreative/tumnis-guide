"""Profile archive and restore on the agent server (P2-18, FR-5.10).

`archive` packs a profile's home into one tar + zstd file under the daemon's
`archives/` folder, with a manifest (relative path, size, sha256 per entry) whose digest
it reports, and moves the profile out of the live list. `restore` unpacks it, checks the
restored tree against the expected manifest digest and puts the profile back, byte for
byte, with its modes and symlinks.
"""

# ruff: noqa: ASYNC240  # small files in tmp_path; blocking reads are fine in these tests

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.conftest import ProfileHome


def _raw_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def _modes(root: Path) -> dict[str, int]:
    """Each entry's permission bits (symlinks: the link itself), by relative path."""
    return {
        str(p.relative_to(root)): stat.S_IMODE(os.lstat(p).st_mode)
        for p in sorted(root.rglob("*"))
        if not p.is_symlink()
    }


def _archive(name: str, archive_id: str) -> object:
    from tumnis_daemon.protocol import Archive, envelope  # noqa: PLC0415

    return Archive(**envelope(f"archive:{archive_id}"), profile=name, archive_id=archive_id)


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_archive_compresses_profile_and_removes_from_live_list(
    tmp_profile_home: ProfileHome,
) -> None:
    """T-P2-18-01
    Archiving `acme-site` writes one archive file under `<state_dir>/archives/`, smaller
    than the profile's files, whose size and sha256 the `archive_done` reports with the
    digest of the profile's manifest taken before; the profile's home is gone from the
    Hermes profiles folder and the profile from the daemon's live list (the one `register`
    sends), though `daemon.toml` names it.
    """
    from tumnis_daemon.archive import handle_archive, live_profiles, manifest_of  # noqa: PLC0415

    ph = tmp_profile_home
    assert ph.name in live_profiles(ph.cfg)
    before = manifest_of(ph.home)
    raw = _raw_size(ph.home)
    archive_id = str(uuid.uuid4())

    done = await handle_archive(_archive(ph.name, archive_id), ph.cfg)  # type: ignore[arg-type]

    assert done.archive_id == archive_id
    assert done.error_code is None
    path = Path(done.path)
    assert path.parent == ph.cfg.state_dir / "archives"
    assert list(path.parent.iterdir()) == [path]
    assert done.size == path.stat().st_size
    assert done.size < raw
    assert done.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert done.manifest_digest == before.digest()
    assert not ph.home.exists()
    assert ph.name not in live_profiles(ph.cfg)


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_restore_is_byte_for_byte(tmp_profile_home: ProfileHome) -> None:
    """T-P2-18-02
    Archive, then restore with the manifest digest the archive reported: `restore_done`
    is ok with the same digest, the restored tree's manifest equals the original one
    (the empty file included), every mode is the same, the symlink is still a link to its
    relative target, and the profile is back in the live list.
    """
    from tumnis_daemon.archive import (  # noqa: PLC0415
        handle_archive,
        handle_restore,
        live_profiles,
        manifest_of,
    )
    from tumnis_daemon.protocol import Restore, envelope  # noqa: PLC0415

    ph = tmp_profile_home
    before = manifest_of(ph.home)
    modes = _modes(ph.home)
    archive_id = str(uuid.uuid4())
    done = await handle_archive(_archive(ph.name, archive_id), ph.cfg)  # type: ignore[arg-type]
    assert not ph.home.exists()

    restore = Restore(
        **envelope(f"restore:{archive_id}"),
        profile=ph.name,
        archive_id=archive_id,
        expected_manifest_digest=done.manifest_digest,
    )
    back = await handle_restore(restore, ph.cfg)

    assert back.ok is True
    assert back.archive_id == archive_id
    assert back.manifest_digest == done.manifest_digest
    after = manifest_of(ph.home)
    assert after == before
    assert ("skills/tumnis/empty.txt", 0, hashlib.sha256(b"").hexdigest()) in after.entries
    assert _modes(ph.home) == modes
    link = ph.home / "latest-session"
    assert link.is_symlink()
    assert os.readlink(link) == "sessions/2026/01.jsonl"
    assert ph.name in live_profiles(ph.cfg)


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_archive_refused_while_a_run_is_active(tmp_profile_home: ProfileHome) -> None:
    """A profile with an active run is not archived: `archive_done` carries
    `error_code="active_run"`, no archive file is written and the home stays in place."""
    from tumnis_daemon.archive import handle_archive, live_profiles  # noqa: PLC0415

    ph = tmp_profile_home
    done = await handle_archive(
        _archive(ph.name, str(uuid.uuid4())),  # type: ignore[arg-type]
        ph.cfg,
        busy=frozenset({ph.name}),
    )

    assert done.error_code == "active_run"
    assert ph.home.is_dir()
    assert not (ph.cfg.state_dir / "archives").exists() or not any(
        (ph.cfg.state_dir / "archives").iterdir()
    )
    assert ph.name in live_profiles(ph.cfg)


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_restore_with_wrong_digest_leaves_profile_archived(
    tmp_profile_home: ProfileHome,
) -> None:
    """A restore whose expected digest does not match the archive's manifest answers
    `ok=False`; the profile stays archived (no home, not live) and its archive file is
    kept for another try."""
    from tumnis_daemon.archive import handle_archive, handle_restore, live_profiles  # noqa: PLC0415
    from tumnis_daemon.protocol import Restore, envelope  # noqa: PLC0415

    ph = tmp_profile_home
    archive_id = str(uuid.uuid4())
    done = await handle_archive(_archive(ph.name, archive_id), ph.cfg)  # type: ignore[arg-type]

    back = await handle_restore(
        Restore(
            **envelope(f"restore:{archive_id}"),
            profile=ph.name,
            archive_id=archive_id,
            expected_manifest_digest="0" * 64,
        ),
        ph.cfg,
    )

    assert back.ok is False
    assert not ph.home.exists()
    assert ph.name not in live_profiles(ph.cfg)
    assert Path(done.path).is_file()
