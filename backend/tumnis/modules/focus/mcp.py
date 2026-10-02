"""focus MCP tools; thin calls into api.py.

focus has no tool of its own. It tells agents' `record_human_reply` (P2-16, FR-8.2) how to
record a reply to a focus message typed in the master's chat channel: `api.relay_reply`,
which answers it as the person's tap in the app does.
"""

from tumnis.modules.agents import api as agents
from tumnis.modules.focus import api

agents.register_reply_handler("focus", api.relay_reply)
