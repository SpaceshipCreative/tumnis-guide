"""agents adapters: one file per outside dependency, each with a fake.

`agents.hermes` (P1-04): the AgentAdapter port; real is HermesAgent (daemon or MCP
endpoint transport), fake is FakeAgent.
"""

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.agents.adapters.fake import FakeAgent
from tumnis.modules.agents.adapters.hermes import HermesAgent
from tumnis.modules.agents.adapters.port import AgentAdapter

ADAPTER_NAME = "agents.hermes"

register_adapter(ADAPTER_NAME, port=AgentAdapter, real=HermesAgent, fake=FakeAgent)
