"""The recording mock MCP servers (P2-11, SAF-6): the Tumnis mock is built from the tool
catalogue (schemas/mcp/v1/tools.json), and every call it answers is recorded with its
arguments. Tests talk to the servers in memory (the SDK's Client), so no socket opens."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest


@pytest.mark.req("SAF-6")
@pytest.mark.wp("P2-11")
def test_mock_built_from_catalogue_records_calls(tmp_path: Path) -> None:
    """T-P2-11-07
    Every tool in schemas/mcp/v1/tools.json is listed by the mock with the catalogue's
    input schema; calling each one answers without error, and the recorder holds one call
    per tool, in order, with server `tumnis`, the tool's name and its arguments exactly,
    and writes the same calls to its JSON-lines file.
    """
    from mcp.client import Client

    from harness import REPO
    from harness.mock_mcp_min import Recorder, build_server, load_catalogue

    catalogue = load_catalogue()
    on_disk = json.loads((REPO / "schemas/mcp/v1/tools.json").read_text(encoding="utf-8"))
    assert [tool["name"] for tool in catalogue] == [tool["name"] for tool in on_disk]
    record = tmp_path / "calls.jsonl"
    recorder = Recorder(record)
    server = build_server(catalogue, recorder)
    sent: list[tuple[str, dict[str, Any]]] = [
        (tool["name"], {"probe": n, "note": "zero\u200bwidth"}) for n, tool in enumerate(catalogue)
    ]

    async def exercise() -> None:
        async with Client(server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            by_name = {tool.name: tool for tool in listed.tools}
            assert set(by_name) == {tool["name"] for tool in catalogue}
            for tool in catalogue:
                assert by_name[tool["name"]].input_schema == tool["input_schema"]
            for name, arguments in sent:
                result = await client.call_tool(name, arguments)
                assert not result.is_error, name

    anyio.run(exercise)

    expected = [("tumnis", name, arguments) for name, arguments in sent]
    assert [(c.server, c.tool, dict(c.arguments)) for c in recorder.calls] == expected
    lines = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert [(r["server"], r["tool"], r["arguments"]) for r in lines] == expected


@pytest.mark.req("SAF-6")
@pytest.mark.wp("P2-11")
def test_worker_mocks_record_calls_and_map_action_classes() -> None:
    """The GitHub, Coolify, Proxmox and Jev mocks list their tools and record each call
    under their own server name; worker calls map to the project policy's action classes,
    and an unknown tool is answered as an error but still recorded."""
    from mcp.client import Client

    from harness.mock_mcp_min import Recorder
    from harness.mock_worker_tools import WORKER_TOOLS, action_class, build_worker_server
    from tumnis.modules.projects.rules import ALLOWED_DEFAULT, GATED_DEFAULT

    recorder = Recorder()

    async def exercise() -> None:
        for server, tools in WORKER_TOOLS.items():
            async with Client(build_worker_server(server, recorder), raise_exceptions=True) as c:
                listed = await c.list_tools()
                assert {t.name for t in listed.tools} == {t["name"] for t in tools}
        async with Client(build_worker_server("github", recorder), raise_exceptions=True) as c:
            merged = await c.call_tool("merge_pull_request", {"repo": "acme/site", "number": 3})
            assert not merged.is_error
            unknown = await c.call_tool("delete_repository", {"repo": "acme/site"})
            assert unknown.is_error

    anyio.run(exercise)
    assert [(c.server, c.tool) for c in recorder.calls] == [
        ("github", "merge_pull_request"),
        ("github", "delete_repository"),
    ]

    expected: dict[tuple[str, str, tuple[tuple[str, Any], ...]], str | None] = {
        ("github", "merge_pull_request", ()): "merge_main",
        ("github", "merge_pull_request", (("base", "feature/footer"),)): None,
        ("github", "push", (("branch", "main"),)): "push_main",
        ("github", "push", (("branch", "refs/heads/master"),)): "push_main",
        ("github", "push", (("branch", "feature/footer"),)): "push_feature_branch",
        ("github", "push", (("branch", "feature/footer"), ("force", True))): "force_push",
        ("github", "create_pull_request", ()): "open_pull_request",
        ("coolify", "deploy", (("environment", "Production"),)): "deploy_production",
        ("coolify", "deploy", (("environment", "preview"),)): "trigger_preview_deploy",
        ("proxmox", "delete_vm", (("vmid", 104),)): "proxmox_delete_guest",
        ("proxmox", "rollback_snapshot", ()): "proxmox_rollback_snapshot",
        ("proxmox", "list_vms", ()): None,
        ("tumnis", "create_task", ()): None,
    }
    vocabulary = set(GATED_DEFAULT) | set(ALLOWED_DEFAULT)
    for (server, tool, args), klass in expected.items():
        assert action_class(server, tool, dict(args)) == klass, (server, tool, args)
        assert klass is None or klass in vocabulary
