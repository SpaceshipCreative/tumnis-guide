"""The api serves the web app (P0-22, FR-7.1): the shell at `/` and at every deep link,
hashed assets cached forever, the service worker and the shell always revalidated, and
`/v1/*` still answering problem+json."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, MasterKeyFile
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

INDEX = "<!doctype html><title>Tumnis Guide</title><main id=main></main>"


def _dist(root: Path) -> Path:
    """A built frontend: index.html, a hashed asset, the service worker and a manifest."""
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "index-3f9a1c2b.js").write_text("console.log('shell')")
    (dist / "sw.js").write_text("self.addEventListener('fetch', () => {})")
    (dist / "manifest.webmanifest").write_text('{"name": "Tumnis Guide"}')
    return dist


@pytest.mark.req("FR-7.1")
@pytest.mark.wp("P0-22")
@pytest.mark.xfail(strict=True, reason="spec:P0-22")
async def test_api_serves_shell_and_deep_links(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    tmp_path: Path,
) -> None:
    """T-P0-22-16
    `GET /` and `GET /projects/<uuid>?view=board` return index.html with
    `Cache-Control: no-cache`; `GET /assets/<hash>.js` is `immutable`; `GET /v1/nope` is a
    problem+json 404; `GET /sw.js` is `no-cache`; a missing asset is 404, not the shell.
    """
    from tests.fixtures import settings_for  # noqa: PLC0415
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import client_at  # noqa: PLC0415

    app = create_app(settings=settings_for(db, dbos_sys_db), clock=clock, shell_dir=_dist(tmp_path))
    try:
        async with client_at(app) as client:
            for path in (
                "/",
                "/projects/0193e5a0-0000-7000-8000-000000000001?view=board",
                "/settings/account",
                "/review",
            ):
                page = await client.get(path)
                assert page.status_code == 200, path
                assert page.text == INDEX, path
                assert page.headers["content-type"].startswith("text/html"), path
                assert page.headers["cache-control"] == "no-cache", path

            asset = await client.get("/assets/index-3f9a1c2b.js")
            assert asset.status_code == 200
            assert "immutable" in asset.headers["cache-control"]
            assert "max-age=31536000" in asset.headers["cache-control"]

            worker = await client.get("/sw.js")
            assert worker.status_code == 200
            assert worker.headers["cache-control"] == "no-cache"

            manifest = await client.get("/manifest.webmanifest")
            assert manifest.status_code == 200

            missing_asset = await client.get("/assets/index-00000000.js")
            assert missing_asset.status_code == 404

            for api_path in ("/v1/nope", "/v1/", "/health/nope", "/mcp/nope"):
                api = await client.get(api_path)
                assert api.status_code == 404, api_path
                assert api.headers["content-type"].startswith("application/problem+json")
                assert api.json()["code"] == "not_found"

            written = await client.post("/projects/x")
            assert written.status_code in {404, 405}
            assert written.headers["content-type"].startswith("application/problem+json")
    finally:
        await core_db.dispose()
