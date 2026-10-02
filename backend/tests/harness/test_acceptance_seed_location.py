"""The acceptance set's knowledge storage location (APP-06, A1.5).

A1.5's Playwright journey drops a PDF on Acme site's composer, and an upload needs a
storage location with a folder for the project (409 `no_location` without one). The
acceptance set (`SeedSet.acceptance`, what `POST /v1/test/reset?set=acceptance` loads)
therefore holds one default location; its writer gives every project of the set its
folder. The backend acceptance `seed` (ACCEPTANCE_WORLD) leaves it out: those tests make
their own MinIO location.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

pytestmark = [pytest.mark.req("A1.5", "FR-15.7"), pytest.mark.wp("SEED")]

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, MasterKeyFile, PepperFile
    from tumnis.core.clock import FixedClock


async def test_acceptance_set_holds_one_default_location() -> None:
    """The acceptance set names one location, the default, on a server folder; the backend
    acceptance world does not."""
    from tumnis.seed import ACCEPTANCE_WORLD, SEED_PATHS, SeedSet, read_seed  # noqa: PLC0415

    locations = [loc for doc in read_seed(SEED_PATHS[SeedSet.acceptance]) for loc in doc.locations]
    assert [(loc.key, loc.kind, loc.is_default) for loc in locations] == [
        ("l_knowledge", "server_path", True)
    ]
    assert [loc for doc in read_seed(ACCEPTANCE_WORLD) for loc in doc.locations] == []


def _rows(db: DbUrls, query: str) -> list[dict[str, Any]]:
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query.encode()).fetchall())


@pytest.mark.integration
@pytest.mark.enable_socket
async def test_acceptance_set_gives_each_project_a_folder_on_its_location(
    db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    pepper_file: PepperFile,
) -> None:
    """Loaded in fakes mode (the compose.test stack), the acceptance set's location is the
    online default, every project has its folder on it, and an upload to `Acme site` finds
    where to go instead of 409 `no_location`."""
    from tests.fixtures import _load_set  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.seed import SEED_PATHS, SeedSet  # noqa: PLC0415

    del fakes, master_key_file, pepper_file
    result = await _load_set(SEED_PATHS[SeedSet.acceptance], db, clock)

    assert result.counts["location"] == 1
    assert _rows(db, "SELECT name, kind, is_default, status FROM storage_locations") == [
        {"name": "Knowledge files", "kind": "server_path", "is_default": True, "status": "online"}
    ]
    folders = _rows(
        db,
        "SELECT p.name FROM project_folders f JOIN projects p ON p.id = f.project_id "
        "JOIN storage_locations l ON l.id = f.location_id ORDER BY p.name",
    )
    assert [f["name"] for f in folders] == ["Acme site", "Beta app", "Gamma ops"]

    ctx = WorkspaceContext(result.ids["ws_main"], SYSTEM_ACTOR)
    await knowledge.check_upload_target(ctx, result.ids["p_acme_site"])
