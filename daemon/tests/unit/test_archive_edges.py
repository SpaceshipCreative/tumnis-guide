"""Profile archive edges (P2-18, FR-5.10): a resent `archive` or `restore` gets the same
answer without redoing anything, `purge_archive` forgets the profile for good, a tampered
pack is never extracted outside its staging folder, and a profile with a claimed run is
busy until the run is released."""

# ruff: noqa: ASYNC240  # small files in tmp_path; blocking reads are fine in these tests

from __future__ import annotations

import io
import json
import tarfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import zstandard

from tumnis_daemon.archive import (
    handle_archive,
    handle_purge,
    handle_restore,
    live_profiles,
    manifest_of,
)
from tumnis_daemon.protocol import Archive, PurgeArchive, Restore, envelope
from tumnis_daemon.state import StateStore

if TYPE_CHECKING:
    from tests.conftest import ProfileHome


def _archive(name: str, archive_id: str) -> Archive:
    return Archive(**envelope(f"archive:{archive_id}"), profile=name, archive_id=archive_id)


def _restore(name: str, archive_id: str, digest: str) -> Restore:
    return Restore(
        **envelope(f"restore:{archive_id}"),
        profile=name,
        archive_id=archive_id,
        expected_manifest_digest=digest,
    )


def _write_pack(path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    path.write_bytes(zstandard.ZstdCompressor().compress(buf.getvalue()))


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
async def test_resent_archive_and_restore_answer_the_same(tmp_profile_home: ProfileHome) -> None:
    ph = tmp_profile_home
    archive_id = str(uuid.uuid4())
    first = await handle_archive(_archive(ph.name, archive_id), ph.cfg)
    again = await handle_archive(_archive(ph.name, archive_id), ph.cfg)

    assert again.error_code is None
    fields = ("path", "size", "sha256", "manifest_digest")
    assert [getattr(again, f) for f in fields] == [getattr(first, f) for f in fields]

    ok = await handle_restore(_restore(ph.name, archive_id, first.manifest_digest), ph.cfg)
    resent = await handle_restore(_restore(ph.name, archive_id, first.manifest_digest), ph.cfg)

    assert ok.ok is True
    assert resent.ok is True
    assert resent.manifest_digest == first.manifest_digest
    assert not Path(first.path).exists()
    assert ph.name in live_profiles(ph.cfg)


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
async def test_missing_home_is_not_found_and_bad_ids_are_refused(
    tmp_profile_home: ProfileHome,
) -> None:
    ph = tmp_profile_home
    missing = await handle_archive(_archive("other-site", str(uuid.uuid4())), ph.cfg)
    bad = await handle_archive(_archive(ph.name, "../escape"), ph.cfg)

    assert missing.error_code == "not_found"
    assert bad.error_code == "invalid_name"
    assert ph.home.is_dir()


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
async def test_purge_forgets_the_archived_profile(tmp_profile_home: ProfileHome) -> None:
    ph = tmp_profile_home
    cfg = replace(ph.cfg, profiles=())  # remembered only, not in daemon.toml
    archive_id = str(uuid.uuid4())
    done = await handle_archive(_archive(ph.name, archive_id), cfg)

    purge = PurgeArchive(
        **envelope(f"purge_archive:{archive_id}"), profile=ph.name, archive_id=archive_id
    )
    await handle_purge(purge, cfg)
    await handle_purge(purge, cfg)  # idempotent

    assert not Path(done.path).exists()
    assert ph.name not in live_profiles(cfg)
    assert ph.name not in json.loads((cfg.state_dir / "profiles.json").read_text())
    assert json.loads((cfg.state_dir / "archived.json").read_text()) == []


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.parametrize("kind", ["hardlink", "escape"])
async def test_tampered_pack_is_refused(tmp_profile_home: ProfileHome, kind: str) -> None:
    ph = tmp_profile_home
    archive_id = str(uuid.uuid4())
    done = await handle_archive(_archive(ph.name, archive_id), ph.cfg)
    # staging is <tmp>/hermes/.tumnis-restore-<id>: two levels up is <tmp>
    outside = ph.cfg.hermes_profiles_dir.parent.parent / "outside.txt"
    outside.write_bytes(b"safe")
    if kind == "hardlink":
        target = tarfile.TarInfo("stolen")
        target.type = tarfile.LNKTYPE
        target.linkname = "../../outside.txt"
        members: list[tuple[tarfile.TarInfo, bytes | None]] = [(target, None)]
        refusal = "FilterError"
    else:
        target = tarfile.TarInfo("../../outside.txt")
        target.size = 4
        members = [(target, b"evil")]
        refusal = "OutsideDestinationError"
    _write_pack(Path(done.path), members)

    back = await handle_restore(_restore(ph.name, archive_id, done.manifest_digest), ph.cfg)

    assert back.ok is False
    assert back.error is not None
    assert back.error.startswith(refusal)
    assert outside.read_bytes() == b"safe"
    assert outside.stat().st_nlink == 1
    assert not ph.home.exists()
    assert ph.name not in live_profiles(ph.cfg)
    assert [p.name for p in ph.cfg.hermes_profiles_dir.parent.iterdir()] == ["profiles"]


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
def test_claimed_run_keeps_its_profile_busy(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state")
    try:
        run_id = uuid.uuid4()
        assert state.claim_run(run_id)
        state.run_profiles[run_id] = "acme-site"
        assert state.busy_profiles() == frozenset({"acme-site"})
        state.release(run_id)
        state.running.discard(run_id)
        assert state.busy_profiles() == frozenset()
    finally:
        state.close()


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
async def test_manifest_matches_the_server_canonical_form(tmp_path: Path) -> None:
    """The digest is the sha256 of `[[path,size,sha256],...]` (no spaces), the form the
    server's `archive_blobs.Manifest` digests."""
    (tmp_path / "a.txt").write_bytes(b"hi")
    manifest = manifest_of(tmp_path)
    sha = "8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4"
    assert manifest.canonical() == f'[["a.txt",2,"{sha}"]]'.encode()
