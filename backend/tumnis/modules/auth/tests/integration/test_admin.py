"""`tumnis admin reset-totp <email>`: a lost phone is recovered from the CLI (P0-13)."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("SEC-1", "SEC-3")
@pytest.mark.wp("P0-13")
def test_reset_totp_from_the_cli(
    db: DbUrls, clock: FixedClock, master_key_file: MasterKeyFile
) -> None:
    """T-P0-13-29
    `tumnis admin reset-totp <email>` prints a new `otpauth://` URI (exit 0); the old
    secret's codes stop working and the new one's work; one `auth.totp_reset` row is
    written in the user's workspace. An unknown email exits 1.
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tests._auth import enroll_totp, secret_from_uri, totp_code  # noqa: PLC0415
    from tests.fixtures import make_user, make_workspace  # noqa: PLC0415
    from tumnis import cli  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import configured, owner_rows  # noqa: PLC0415
    from tumnis.modules.auth.providers import check_totp  # noqa: PLC0415

    workspace = make_workspace(db)
    user_id, email = make_user(db, workspace)

    async def enroll() -> str:
        async with configured(db):
            return await enroll_totp(user_id, workspace, clock.now())

    old = asyncio.run(enroll())
    env = {
        "DATABASE_URL": db.app,
        "DATABASE_DIRECT_URL": db.app,
        "DEPLOYMENT_ENV": "dev",
        "MASTER_KEY_FILE": str(master_key_file.path),
    }
    result = CliRunner().invoke(cli.app, ["admin", "reset-totp", email], env=env)
    assert result.exit_code == 0, result.output
    found = re.search(r"otpauth://totp/\S+", result.output)
    assert found is not None, result.output
    new = secret_from_uri(found.group(0))
    assert new != old

    async def check(secret: str) -> str:
        async with configured(db):
            return await check_totp(
                user_id, workspace, totp_code(secret, clock.now()), clock.now(), confirmed=True
            )

    assert asyncio.run(check(old)) == "invalid"
    assert asyncio.run(check(new)) == "ok"
    rows = owner_rows(
        db,
        "SELECT workspace_id, details::text FROM audit_log WHERE action = 'auth.totp_reset'",
    )
    assert len(rows) == 1, rows
    assert rows[0][0] == workspace
    assert new not in rows[0][1]

    unknown = CliRunner().invoke(cli.app, ["admin", "reset-totp", "nobody@example.test"], env=env)
    assert unknown.exit_code == 1, unknown.output
