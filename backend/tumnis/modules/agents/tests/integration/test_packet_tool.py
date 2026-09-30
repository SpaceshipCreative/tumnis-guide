"""The `get_task_packet` tool and its REST twin (P2-02, FR-5.4, R-24)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-5.4")
@pytest.mark.wp("P2-02")
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


@pytest.mark.req("FR-5.4")
@pytest.mark.wp("P2-02")
async def test_get_task_packet_leaves_context_items_out_without_context_read(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """Context items are outside content under `context:read`: a caller with only
    `tasks:read` gets the task's packet without them (on MCP and REST alike), while a
    caller that holds `context:read` gets them."""
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
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415

    world = await make_world(workspace, clock)
    callers = Callers(app, world)
    task = await world.task("A", title="Check the Acme footer", label="ai", estimate_minutes=None)
    url = "https://example.com/acme-footer-notes"
    await integrations.link_context(
        workspace.ctx,
        owner_type="task",
        owner_id=task.id,
        target_type="url",
        target_url=url,
        added_by=workspace.ctx.actor,
    )
    op = agent_surface.get_op("get_task_packet")
    full, _ = await callers.key(ALL_SCOPES)
    narrow, _ = await callers.key(frozenset({"tasks:read"}))

    async with mcp_running(app), http_for(app, full) as http:
        seen = await mcp_call(http, "get_task_packet", {"task_id": str(task.id)})
    assert seen.ok, seen
    assert [item["provider_url"] for item in seen.data["body"]["context_items"]] == [url]
    assert url in seen.data["prompt_text"]

    async with mcp_running(app), http_for(app, narrow) as http:
        via_mcp = await mcp_call(http, "get_task_packet", {"task_id": str(task.id)})
        via_rest = await rest_call(http, op, {"task_id": str(task.id)})
    for answer in (via_mcp, via_rest):
        assert answer.ok, answer
        assert answer.data["body"]["context_items"] == []
        assert url not in answer.data["prompt_text"]
        assert url not in json.dumps(answer.data["body"])
