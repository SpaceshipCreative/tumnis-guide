"""The worker's OAuth client follows the deployment's SSRF policy (P3-02, SEC): a
self-hosted MCP server on the LAN is reachable when the deployment allows it, and the
hosted policy is only the default for a process that configured nothing."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tumnis.core.net import NetPolicy
from tumnis.modules.integrations import adapters as _adapters  # noqa: F401  (registers the port)
from tumnis.modules.integrations import workflows
from tumnis.modules.integrations.adapters.oauth import McpOAuthClient


@pytest.fixture
def real_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TUMNIS_ADAPTERS", "real")
    yield
    workflows.configure_net_policy(None)


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
def test_the_oauth_client_uses_the_configured_policy(real_mode: None) -> None:
    policy = NetPolicy(mode="self-hosted")
    workflows.configure_net_policy(policy)
    port = workflows._oauth_port()  # the seam under test
    assert isinstance(port, McpOAuthClient)
    assert port._policy is policy


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
def test_without_a_configured_policy_the_client_is_hosted(real_mode: None) -> None:
    workflows.configure_net_policy(None)
    port = workflows._oauth_port()
    assert isinstance(port, McpOAuthClient)
    assert port._policy.mode == "hosted"
