"""The tool inventory: every tool the PRD names is registered, or pending with the WP that
brings it, and nothing else is (P2-01, FR-14.10, R-35). Unit layer: the registry loads
without a database.
"""

from __future__ import annotations

import re
from typing import Any, Final

import pytest

# PRD "MCP server" row (docs/PRD.md, Agent architecture), in its order.
PRD_TOOLS: Final = (
    "list_tasks",
    "get_task_packet",
    "update_task_status",
    "post_result",
    "ask_human",
    "request_approval",
    "get_project_context",
    "create_task",
    "update_estimate",
    "search",
    "search_knowledge",
    "get_document",
    "add_document",
    "get_context_item",
    "draft_reply",
    "ingest_items",
    "get_project_digest",
    "get_workspace_digest",
    "delegate_task",
    "wait_for_task",
)
# Plan additions beyond the PRD list, both master-only (R-35).
PLAN_ADDITIONS: Final = {"pause_agents": "P2-09", "record_human_reply": "P2-16"}
FORBIDDEN: Final = re.compile(r"hermes|\bsoul\b|soul\.md|profile home|hindsight", re.IGNORECASE)
WP_ID: Final = re.compile(r"^P\d-\d\d$")


def _registry() -> tuple[dict[str, Any], dict[str, str]]:
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.wiring import load_mcp  # noqa: PLC0415

    load_mcp()
    return {op.name: op for op in agent_surface.ops()}, dict(agent_surface.PENDING_TOOLS)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
def test_prd_tools_registered_or_pending() -> None:
    """T-P2-01-16
    Every PRD tool is registered or listed in `PENDING_TOOLS` with the WP that brings it
    (never both); nothing is registered or pending outside the PRD list plus the two
    master-only plan additions, which are master-only once registered."""
    registered, pending = _registry()
    allowed = set(PRD_TOOLS) | set(PLAN_ADDITIONS)
    for name in PRD_TOOLS:
        assert (name in registered) != (name in pending), name
    assert set(registered) <= allowed, sorted(set(registered) - allowed)
    assert set(pending) <= allowed, sorted(set(pending) - allowed)
    for name, wp in pending.items():
        assert WP_ID.match(wp), (name, wp)
    for name, wp in PLAN_ADDITIONS.items():
        if name in registered:
            assert registered[name].master_only, name
        else:
            assert pending.get(name) == wp, name


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
def test_no_hermes_specific_names() -> None:
    """T-P2-01-17
    No tool name, description, input or output field name or field description mentions
    Hermes, SOUL, a profile home or Hindsight: the surface is for any agent."""
    registered, _pending = _registry()
    assert registered
    found: list[str] = []
    for op in registered.values():
        texts = [op.name, op.description]
        for model in (op.input_model, op.output_model):
            for name, field in model.model_fields.items():
                texts.extend([name, field.description or ""])
        found.extend(f"{op.name}: {text!r}" for text in texts if FORBIDDEN.search(text))
    assert found == []
