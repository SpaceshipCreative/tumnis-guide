"""integrations adapters: one file per outside dependency, each with a fake.

Connectors register here through `register_connector` (adapter name
`integrations.connector.<provider>`). The scripted connector is a demo with no outside
dependency: its real side is the same class, and its contract runs on recordings.
"""

from tumnis.modules.integrations.adapters.fake import ScriptedConnector
from tumnis.modules.integrations.api import register_connector

register_connector("scripted", "email", real=ScriptedConnector, fake=ScriptedConnector)
