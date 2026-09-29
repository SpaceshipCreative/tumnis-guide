"""The Prometheus registry every counter registers on (P0-08; P0-27 serves it on /metrics)."""

from prometheus_client import CollectorRegistry, Counter

REGISTRY = CollectorRegistry(auto_describe=True)

CACHE_HITS = Counter(
    "tumnis_cache_hits", "Cache lookups that found an entry", ["cache"], registry=REGISTRY
)
CACHE_MISSES = Counter(
    "tumnis_cache_misses", "Cache lookups that found no entry", ["cache"], registry=REGISTRY
)
