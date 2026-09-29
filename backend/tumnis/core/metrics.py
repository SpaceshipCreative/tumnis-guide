"""Prometheus registry (P0-27 adds the /metrics route and the request and queue metrics).

P0-15 adds the audit chain gauges (tumnis.core.audit_workflows).
"""

from prometheus_client import CollectorRegistry

REGISTRY = CollectorRegistry(auto_describe=True)
