"""integrations adapters: one file per outside dependency, each with a fake.

Connectors register here through `register_connector` (adapter name
`integrations.connector.<provider>`). The scripted connector is a demo with no outside
dependency: its real side is the same class, and its contract runs on recordings.

P3-02: `integrations.oauth` is the OAuth port of MCP providers (`oauth.py` over HTTP,
`fake_oauth.py` in memory), and provider `fake` is the sync framework's scriptable source
(listed only when TUMNIS_ADAPTERS=fake).
"""

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.integrations.adapters.fake import ScriptedConnector
from tumnis.modules.integrations.adapters.fake_oauth import RESOURCE, FakeOAuthServer
from tumnis.modules.integrations.adapters.fake_source import FakeSource
from tumnis.modules.integrations.adapters.oauth import McpOAuthClient
from tumnis.modules.integrations.api import ProviderSpec, register_connector, register_provider
from tumnis.modules.integrations.oauth_port import ADAPTER as OAUTH_ADAPTER
from tumnis.modules.integrations.oauth_port import OAuthPort

register_connector("scripted", "email", real=ScriptedConnector, fake=ScriptedConnector)
register_connector("fake", "email", real=FakeSource, fake=FakeSource)
register_adapter(OAUTH_ADAPTER, port=OAuthPort, real=McpOAuthClient, fake=FakeOAuthServer)
register_provider(ProviderSpec("fake", "email", "Fake mail", server_url=RESOURCE, fake_only=True))
