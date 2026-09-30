"""The `get_task_packet` tool and its REST twin (P2-02, FR-5.4, R-24)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-5.4")
@pytest.mark.wp("P2-02")
@pytest.mark.xfail(strict=True, reason="spec:P2-02")
async def test_get_task_packet_has_no_token(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P2-02-14
    `get_task_packet` returns the task's packet (kind `task`, the task, its project's
    brief and the policy) with `callback.task_token: null`, the same on MCP and on
    `GET /v1/tasks/{task_id}/packet`.
    """
    from tests._mcp import (  # noqa: PLC0415
        ALL_SCOPES,
        Callers,
        http_for,
        make_world,
        mcp_call,
        mcp_running,
        rest_call,
    )
    from tumnis.core import agent_surface  # noqa: PLC0415

    world = await make_world(workspace, clock)
    callers = Callers(app, world)
    secret, _key_id = await callers.key(ALL_SCOPES)
    task = await world.task("A", title="Check the Acme footer", label="ai", estimate_minutes=None)
    op = agent_surface.get_op("get_task_packet")

    async with mcp_running(app), http_for(app, secret) as http:
        via_mcp = await mcp_call(http, "get_task_packet", {"task_id": str(task.id)})
        via_rest = await rest_call(http, op, {"task_id": str(task.id)})
    assert via_mcp.ok, via_mcp
    assert via_rest.ok, via_rest
    assert via_mcp.data == via_rest.data
    packet = via_mcp.data
    assert packet["kind"] == "task"
    assert packet["callback"]["task_token"] is None
    assert packet["callback"]["token_valid_until"] == "run_end"
    assert packet["body"]["task"]["id"] == str(task.id)
    assert "Check the Acme footer" in packet["body"]["task"]["text"]["rendered"]
    assert packet["body"]["project"]["id"] == str(world.projects["A"])
    assert packet["policy"]["gated"]
    assert "tmt_" not in packet["prompt_text"]
