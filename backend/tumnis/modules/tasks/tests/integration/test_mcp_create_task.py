"""`create_task` through MCP applies the subtask rules (P2-01, FR-14.10, FR-3.8, FR-4.4).

An agent's Human or Hybrid subtask needs an estimate (422 `estimate_required`), and the
server lays each subtask out against the project's card threshold (30 here): under it a
Human subtask is a checklist item on its parent's card, at or over it its own card, and an
AI subtask always nests on the parent (`nested_ai`) with its estimate dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _subtask(http: Any, world: Any, **fields: Any) -> Any:
    from tests._mcp import idem, mcp_call  # noqa: PLC0415

    args = {
        "project_id": str(world.projects["A"]),
        "parent_id": str(world.parents["A"]),
        "title": "A subtask",
        "idempotency_key": idem(),
        **fields,
    }
    return await mcp_call(http, "create_task", args)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
async def test_human_or_hybrid_subtask_without_estimate_rejected(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock, key_client: Any
) -> None:
    """T-P2-01-14
    A key-authenticated agent creating a Human or a Hybrid subtask without
    `estimate_minutes` gets `estimate_required`."""
    from tests._mcp import http_for, make_world, mcp_running  # noqa: PLC0415

    world = await make_world(workspace, clock)
    keyed = await key_client(frozenset({"tasks:read", "tasks:write"}))
    async with mcp_running(app), http_for(app, keyed.key) as http:
        for label in ("human", "hybrid"):
            refused = await _subtask(http, world, label=label)
            assert refused.code == "estimate_required", (label, refused)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
async def test_card_threshold_applied(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock, key_client: Any
) -> None:
    """T-P2-01-15
    Against a threshold of 30, Human subtasks estimated 29, 30 and 31 are laid out as
    checklist, card and card; an AI subtask sent with `estimate_minutes=20` is `nested_ai`
    with `estimate_minutes: null`."""
    from tests._mcp import http_for, make_world, mcp_running  # noqa: PLC0415

    world = await make_world(workspace, clock)
    keyed = await key_client(frozenset({"tasks:read", "tasks:write"}))
    async with mcp_running(app), http_for(app, keyed.key) as http:
        layouts = []
        for minutes in (29, 30, 31):
            made = await _subtask(http, world, label="human", estimate_minutes=minutes)
            assert made.ok, made
            layouts.append(made.data["layout"])
        ai = await _subtask(http, world, label="ai", estimate_minutes=20)
    assert layouts == ["checklist", "card", "card"]
    assert ai.ok, ai
    assert ai.data["layout"] == "nested_ai"
    assert ai.data["estimate_minutes"] is None
