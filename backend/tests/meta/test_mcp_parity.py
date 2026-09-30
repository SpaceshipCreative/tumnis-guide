"""REST and MCP are two doors onto the same functions (P2-01, FR-14.10, FR-14.9).

Every op in the registry (`tumnis.core.agent_surface`) is an MCP tool and a REST twin
under /v1. These sweeps iterate the registry, so a tool a later WP registers is covered
with no edit here: its twin exists, both take and answer the same schema (normalized by
tests/meta/_schema_norm.py), a read gives the same JSON through both doors, and the
committed tool catalogue matches the registry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.meta._schema_norm import norm

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._mcp import Callers, World

REPO = Path(__file__).resolve().parents[3]
CATALOGUE = REPO / "schemas" / "mcp" / "v1" / "tools.json"


def _ops() -> list[Any]:
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.wiring import load_mcp  # noqa: PLC0415

    load_mcp()
    return agent_surface.ops()


def _operation(spec: dict[str, Any], op: Any) -> dict[str, Any] | None:
    return spec["paths"].get(op.rest_path, {}).get(op.rest_method.lower())


def _twin_input(spec: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    """The twin's path + query parameters and body properties as one object schema."""
    components = spec.get("components", {}).get("schemas", {})
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in operation.get("parameters", []):
        if param["in"] not in ("path", "query"):
            continue
        properties[param["name"]] = param["schema"]
        if param.get("required"):
            required.append(param["name"])
    body = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if body is not None:
        resolved = norm(body["schema"], {}, components)
        properties.update(resolved.get("properties", {}))
        required.extend(resolved.get("required", []))
    return norm({"type": "object", "properties": properties, "required": required}, {}, components)


def _twin_output(spec: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    components = spec.get("components", {}).get("schemas", {})
    responses = operation["responses"]
    ok = responses.get("200") or responses.get("201")
    return norm(ok["content"]["application/json"]["schema"], {}, components)


@pytest.fixture
async def listing_key(surface_app: FastAPI, callers: Callers) -> str:
    """A key with every scope (tools/list needs a bearer)."""
    from tests._mcp import ALL_SCOPES  # noqa: PLC0415

    secret, _key_id = await callers.key(ALL_SCOPES)
    return secret


async def _listed_tools(app: FastAPI, key: str) -> dict[str, dict[str, Any]]:
    from tests._mcp import http_for, mcp_tools  # noqa: PLC0415

    async with http_for(app, key) as http:
        return await mcp_tools(http)


@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
async def test_every_tool_has_a_rest_twin(surface_app: FastAPI, listing_key: str) -> None:
    """T-P2-01-01
    Every registered op maps to exactly one OpenAPI operation at its `rest_method
    rest_path`, and the MCP server lists it as a tool of the same name."""
    ops = _ops()
    assert ops, "the registry is empty"
    spec = surface_app.openapi()
    missing = [
        f"{op.name}: {op.rest_method} {op.rest_path}" for op in ops if not _operation(spec, op)
    ]
    assert missing == [], f"ops without a REST twin: {missing}"
    twins = [(op.rest_method, op.rest_path) for op in ops]
    assert len(set(twins)) == len(twins), "two ops share one REST operation"
    names = [op.name for op in ops]
    assert len(set(names)) == len(names)
    tools = await _listed_tools(surface_app, listing_key)
    assert sorted(tools) == sorted(names)


@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
async def test_tool_input_schema_equals_twin_input(surface_app: FastAPI, listing_key: str) -> None:
    """T-P2-01-02
    For each op, the normalized MCP `inputSchema` equals the twin's path + query + body
    schema, plus `idempotency_key` (a required string) when the op writes."""
    spec = surface_app.openapi()
    tools = await _listed_tools(surface_app, listing_key)
    diffs = []
    for op in _ops():
        tool = norm(tools[op.name]["inputSchema"])
        expected = _twin_input(spec, _operation(spec, op) or {})
        if op.write:
            key = tool["properties"].pop("idempotency_key", None)
            assert key is not None, op.name
            assert key.get("type") == "string", op.name
            assert "idempotency_key" in tool.get("required", []), op.name
            tool["required"] = [r for r in tool["required"] if r != "idempotency_key"]
            if not tool["required"]:
                del tool["required"]
        tool.pop("additionalProperties", None)
        expected.pop("additionalProperties", None)
        if tool != expected:
            diffs.append(
                f"{op.name}:\n  mcp  {json.dumps(tool, sort_keys=True)}\n"
                f"  rest {json.dumps(expected, sort_keys=True)}"
            )
    assert diffs == [], "\n".join(diffs)


@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
async def test_tool_output_schema_equals_twin_response(
    surface_app: FastAPI, listing_key: str
) -> None:
    """T-P2-01-03
    For each op, the normalized MCP `outputSchema` equals the twin's 200 or 201 response
    schema."""
    spec = surface_app.openapi()
    tools = await _listed_tools(surface_app, listing_key)
    diffs = []
    for op in _ops():
        tool = norm(tools[op.name].get("outputSchema"))
        expected = _twin_output(spec, _operation(spec, op) or {})
        if tool != expected:
            diffs.append(
                f"{op.name}:\n  mcp  {json.dumps(tool, sort_keys=True)}\n"
                f"  rest {json.dumps(expected, sort_keys=True)}"
            )
    assert diffs == [], "\n".join(diffs)


@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
async def test_same_call_same_result_on_both_doors(
    surface_app: FastAPI, world: World, callers: Callers
) -> None:
    """T-P2-01-04
    For each read op, the same input with the same key returns equal JSON on both doors."""
    from tests._mcp import ALL_SCOPES, SAMPLES, http_for, mcp_call, rest_call  # noqa: PLC0415

    secret, _key_id = await callers.key(ALL_SCOPES)
    await world.task("A", title="surface parity task")
    reads = [op for op in _ops() if not op.write]
    assert reads, "no read op registered"
    async with http_for(surface_app, secret) as http:
        for op in reads:
            assert op.name in SAMPLES, f"add a sample for {op.name} in tests/_mcp.py"
            args = await SAMPLES[op.name](world, "A")
            via_mcp = await mcp_call(http, op.name, args)
            via_rest = await rest_call(http, op, args)
            assert via_mcp.ok, (op.name, via_mcp)
            assert via_rest.ok, (op.name, via_rest)
            assert via_mcp.data == via_rest.data, op.name


@pytest.mark.contract
@pytest.mark.req("FR-14.9")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
def test_catalogue_is_committed() -> None:
    """T-P2-01-20
    `schemas/mcp/v1/tools.json` (name, scope, input and output schema per tool; the mock
    MCP server's input, P2-12) equals what the registry produces: `make gen` writes it."""
    from tumnis.core import mcp_server  # noqa: PLC0415

    _ops()
    produced = mcp_server.catalogue_json()
    assert CATALOGUE.is_file(), "run make gen"
    assert CATALOGUE.read_text() == produced
