"""agents adapters: one file per outside dependency, each with a fake.

`agents.hermes` (P1-04): the AgentAdapter port; real is HermesAgent (daemon or MCP
endpoint transport), fake is FakeAgent. Its fake is scriptable across processes as
`runner` (`POST /v1/test/fakes/runner/script`, R-37, P2-04).
"""

from tumnis.core.adapters.registry import register_adapter
from tumnis.core.fake_scripts import RUNNER, register_fake_script
from tumnis.modules.agents.adapters.fake import FakeAgent, parse_runner_script
from tumnis.modules.agents.adapters.hermes import HermesAgent
from tumnis.modules.agents.adapters.port import AgentAdapter

ADAPTER_NAME = "agents.hermes"

register_adapter(ADAPTER_NAME, port=AgentAdapter, real=HermesAgent, fake=FakeAgent)
register_fake_script(RUNNER, parse_runner_script)
