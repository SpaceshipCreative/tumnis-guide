"""tasks event payload models and subscribers (P0-18).

Interfaces only until the P0-18 spec tests turn green.
"""

from tumnis.core.events import EventEnvelope


async def create_default_columns(envelope: EventEnvelope) -> None:
    raise NotImplementedError
