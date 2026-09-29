"""Every SEC-3 action writes one audit row, in the same transaction as the action (P0-15)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.audit_cases import AUDIT_CASES, AuditCase

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.core.tests.integration._audit import Ctx

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
async def audit_ctx(app: FastAPI, db: DbUrls, clock: FixedClock) -> AsyncIterator[Ctx]:
    from tumnis.core.tests.integration._audit import audit_ctx as build  # noqa: PLC0415

    async with build(app, db, clock) as ctx:
        yield ctx


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
@pytest.mark.parametrize("case", AUDIT_CASES, ids=[c.action for c in AUDIT_CASES])
async def test_each_action_writes_one_row(
    case: AuditCase, audit_ctx: Ctx, db: DbUrls, clock: FixedClock
) -> None:
    """T-P0-15-07
    Performing the case's operation through the real route writes exactly one row with
    that action, its `actor_type` and actor id, `occurred_at` from the clock, the source
    address, and the `X-Request-ID` the client sent as `correlation_id`.
    """
    from tumnis.core.tests.integration._audit import owner_rows  # noqa: PLC0415

    await case.perform(audit_ctx)

    rows = owner_rows(
        db,
        "SELECT workspace_id, actor_type, actor_id, occurred_at, host(source_ip), "
        "correlation_id FROM audit_log WHERE action = %s",
        (case.action,),
    )
    assert len(rows) == 1, rows
    workspace_id, actor_type, actor_id, occurred_at, source_ip, correlation_id = rows[0]
    assert workspace_id == audit_ctx.workspace_id
    assert actor_type == case.actor_type
    assert actor_id == audit_ctx.actor_id(case.actor_type)
    assert occurred_at == clock.now()
    assert source_ip == audit_ctx.source_ip
    assert correlation_id == audit_ctx.request_id


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
async def test_action_rolled_back_leaves_no_audit_row(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-15-08
    An operation that fails after `record` in the same transaction leaves no row (and no
    used seq): the same operation succeeding afterwards is seq 1.
    """
    from tumnis.core import audit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import configured, owner_rows  # noqa: PLC0415

    class ActionFailedError(Exception):
        pass

    async def failing_action() -> None:
        async with tenant_session(workspace.ctx) as s:
            await audit.record(
                s, "settings.changed", details={"key": "timezone"}, occurred_at=clock.now()
            )
            raise ActionFailedError

    async with configured(db):
        with pytest.raises(ActionFailedError):
            await failing_action()
        assert owner_rows(db, "SELECT count(*) FROM audit_log") == [(0,)]

        async with tenant_session(workspace.ctx) as s:
            await audit.record(
                s, "settings.changed", details={"key": "timezone"}, occurred_at=clock.now()
            )
    assert owner_rows(db, "SELECT seq, action FROM audit_log") == [(1, "settings.changed")]


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
@pytest.mark.xfail(strict=True, reason="spec:P0-14")
async def test_real_actions_store_no_secrets(audit_ctx: Ctx, db: DbUrls) -> None:
    """T-P0-15-10
    After a login, a key create and a secret setting change, the three actions are audited
    and no `details` value holds the password, the key secret or the setting's plaintext.
    Waits on P0-13 (login), P0-14 (keys) and P0-08 (encrypted settings).
    """
    from tumnis.core.tests.integration._audit import (  # noqa: PLC0415
        change_secret_setting,
        create_key,
        owner_rows,
        sign_in,
    )

    password = await sign_in(audit_ctx)
    secret = await create_key(audit_ctx)
    plaintext = await change_secret_setting(audit_ctx)

    rows = owner_rows(db, "SELECT action, details FROM audit_log")
    assert {"auth.login", "key.created", "settings.changed"} <= {action for action, _ in rows}
    stored = json.dumps([details for _, details in rows])
    for value in (password, secret, plaintext):
        assert value
        assert value not in stored
