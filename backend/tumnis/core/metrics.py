"""Prometheus registry (P0-27 adds the /metrics route and the request and queue metrics).

P0-28 adds the operations gauges, refreshed from the database at scrape time.
"""

from prometheus_client import CollectorRegistry
from sqlalchemy.ext.asyncio import AsyncEngine

REGISTRY = CollectorRegistry(auto_describe=True)


async def refresh_ops_gauges(engine: AsyncEngine) -> None:
    raise NotImplementedError
