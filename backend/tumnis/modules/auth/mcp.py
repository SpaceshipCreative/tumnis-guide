"""auth MCP tools; thin calls into api.py.

auth has no tools of its own. It tells the agent surface which run a task token belongs
to (P2-01, R-31): a caller with a run writes untainted, a key with no run does not.
"""

from tumnis.core import agent_surface as surface
from tumnis.core.principal import Principal
from tumnis.modules.auth import api


async def _task_token_facts(principal: Principal) -> surface.CallerFacts | None:
    run_id = await api.task_token_run(principal)
    return None if run_id is None else surface.CallerFacts(run_id=run_id)


surface.register_caller_facts("auth.task_token", _task_token_facts)
