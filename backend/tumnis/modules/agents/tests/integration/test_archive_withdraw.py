"""Withdrawing a runner command (P2-18, FR-5.10): an `archive` or `restore` the agent
server never received (still `queued`) is taken back after a wait slice, so one offline
runner cannot hold the `archive` queue's only slot; one the runner received stays."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
async def test_withdraw_takes_back_only_a_command_never_received(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls
) -> None:
    from tests.fakes.fake_runner import create_runner  # noqa: PLC0415
    from tumnis.modules.agents import archive  # noqa: PLC0415

    runner_id, _token = create_runner(workspace, clock, "homelab-hermes")
    queued, sent = archive.archive_message_id("a-queued"), archive.archive_message_id("a-sent")
    with psycopg.connect(db.libpq(OWNER)) as conn:
        for message_id, status in ((queued, "queued"), (sent, "sent")):
            conn.execute(
                b"INSERT INTO runner_messages (workspace_id, runner_id, message_id, direction,"
                b" type, payload, status, created_by) VALUES (%s, %s, %s, 'out', 'archive',"
                b" '{}'::jsonb, %s, 'system')",
                (workspace.id, runner_id, message_id, status),
            )

    assert await archive.withdraw_command(workspace.id, "archive", "a-queued") is True
    assert await archive.withdraw_command(workspace.id, "archive", "a-queued") is False
    assert await archive.withdraw_command(workspace.id, "archive", "a-sent") is False
    with psycopg.connect(db.libpq(OWNER)) as conn:
        rows = conn.execute(
            b"SELECT message_id, deleted_at IS NOT NULL FROM runner_messages"
        ).fetchall()
    assert dict(rows) == {queued: True, sent: False}
