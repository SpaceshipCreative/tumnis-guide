"""The `unattended-tick` test tick (P4-04, R-37; fakes only): `POST
/v1/test/tick/unattended-tick` runs one unattended tick at the server clock's time in the
api process, the same work the scheduled `unattended_tick` workflow does. Registered at
import; the router imports this module so the api process has it."""

from datetime import datetime
from typing import Any, Final

from tumnis.core.ticks import register_tick
from tumnis.modules.planning import api

TICK_NAME: Final = "unattended-tick"


async def unattended(client: Any, now: datetime) -> int:
    """One tick at `now` (no DBOS client needed); how many runs it started."""
    del client
    return await api.run_unattended_tick(now)


register_tick(TICK_NAME, unattended)
