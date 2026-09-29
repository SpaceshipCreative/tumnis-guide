"""The cache interface, the in-process backend and the cache registry (P0-08, Caching).

Every registered cache gets the write-visible test for free: T-P0-08-01 is parametrized
over `registered_caches()` after every module has registered its caches (tumnis.wiring).
"""

from __future__ import annotations

import uuid

import pytest

from tumnis.core.clock import FixedClock

WORKSPACE = uuid.UUID("01890000-0000-7000-8000-00000000000a")
# A system-scope namespace registered by this test module, for T-P0-08-01 and T-P0-08-03.
SYSTEM_PROBE = "test.system_probe"


def _register_system_probe() -> None:
    from tumnis.core.cache import CacheSpec, register_cache  # noqa: PLC0415

    register_cache(CacheSpec(SYSTEM_PROBE, "system", None, ("test only",)))


def registered_cache_names() -> list[str]:
    """Collection-time list of every registered cache. Before the registry exists, one
    placeholder keeps the test visible (and failing) instead of silently empty."""
    try:
        import tumnis.wiring  # noqa: F401, PLC0415  # modules register their caches
        from tumnis.core.cache import registered_caches  # noqa: PLC0415

        _register_system_probe()
        names = [spec.name for spec in registered_caches()]
    except (ImportError, NotImplementedError):
        names = []
    return names or ["<no cache registry yet>"]


@pytest.mark.req("Caching")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
@pytest.mark.parametrize("name", registered_cache_names())
async def test_every_registered_cache_write_is_visible_on_next_read(
    name: str, clock: FixedClock
) -> None:
    """T-P0-08-01
    For each registered cache: `set` then `get` returns the value; an invalidation makes
    the next `get` miss.
    """
    from tumnis.core.cache import (  # noqa: PLC0415
        CacheKey,
        InProcessCache,
        named_cache,
        use_backend,
    )

    cache = named_cache(name)
    if cache.spec.scope == "workspace":
        key = CacheKey.for_workspace(WORKSPACE, name, "probe")
    else:
        key = CacheKey.system(name, "probe")

    with use_backend(InProcessCache(clock)):
        assert await cache.get(key) is None
        await cache.set(key, b"first")
        assert await cache.get(key) == b"first"
        await cache.set(key, b"second")
        assert await cache.get(key) == b"second"
        await cache.invalidate(key)
        assert await cache.get(key) is None


@pytest.mark.req("Caching")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
def test_every_registered_cache_declares_an_invalidation_rule() -> None:
    """T-P0-08-02
    `invalidated_by` is non-empty for every registered cache spec, and the settings cache
    is among them.
    """
    import tumnis.wiring  # noqa: F401, PLC0415
    from tumnis.core.cache import registered_caches  # noqa: PLC0415

    specs = registered_caches()
    assert "settings" in {spec.name for spec in specs}
    for spec in specs:
        assert spec.invalidated_by, spec.name
        assert all(rule.strip() for rule in spec.invalidated_by), spec.name


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
def test_key_without_workspace_prefix_is_rejected() -> None:
    """T-P0-08-03
    `CacheKey("tasks:1")` raises; a workspace key needs a real workspace id; `system` keys
    exist only for namespaces registered with scope "system".
    """
    from tumnis.core.cache import CacheKey, InvalidCacheKey  # noqa: PLC0415

    _register_system_probe()
    for bad in ("tasks:1", "", "ws::tasks:1", "ws:not-a-uuid:tasks:1", f"ws:{WORKSPACE}"):
        with pytest.raises(InvalidCacheKey):
            CacheKey(bad)
    with pytest.raises(InvalidCacheKey):
        CacheKey.system("settings", "x")  # a workspace-scope namespace
    with pytest.raises(InvalidCacheKey):
        CacheKey.system("nobody.registered", "x")

    key = CacheKey.for_workspace(WORKSPACE, "tasks", "1")
    assert key.value == f"ws:{WORKSPACE}:tasks:1"
    assert CacheKey(key.value) == key
    assert CacheKey.system(SYSTEM_PROBE, "abc").value != CacheKey.system(SYSTEM_PROBE, "abd").value


@pytest.mark.req("Caching")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
async def test_ttl_and_tags_follow_the_clock(clock: FixedClock) -> None:
    """T-P0-08-04
    An entry expires when the FixedClock passes its TTL (not before); an entry without a
    TTL stays; `invalidate_tag` drops every key carrying the tag and nothing else.
    """
    from tumnis.core.cache import CacheKey, InProcessCache  # noqa: PLC0415

    cache = InProcessCache(clock)
    short, forever, tagged, both = (
        CacheKey.for_workspace(WORKSPACE, "test", part) for part in ("short", "forever", "a", "b")
    )
    await cache.set(short, b"s", ttl_s=60)
    await cache.set(forever, b"f")
    clock.advance(seconds=59)
    assert await cache.get(short) == b"s"
    clock.advance(seconds=2)
    assert await cache.get(short) is None
    clock.advance(days=365)
    assert await cache.get(forever) == b"f"

    await cache.set(tagged, b"a", tags=["project:1"])
    await cache.set(both, b"b", tags=["project:1", "project:2"])
    await cache.invalidate_tag("project:1")
    assert await cache.get(tagged) is None
    assert await cache.get(both) is None
    assert await cache.get(forever) == b"f"

    await cache.set(both, b"b2", tags=["project:2"])
    await cache.invalidate_tag("project:1")  # the old tag no longer points at the key
    assert await cache.get(both) == b"b2"


@pytest.mark.req("Caching")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
async def test_hit_and_miss_counters_are_exported(clock: FixedClock) -> None:
    """T-P0-08-16
    `tumnis_cache_hits_total` and `tumnis_cache_misses_total`, labelled with the cache name,
    rise on hits and misses.
    """
    import tumnis.wiring  # noqa: F401, PLC0415
    from tumnis.core.cache import (  # noqa: PLC0415
        CacheKey,
        InProcessCache,
        named_cache,
        use_backend,
    )
    from tumnis.core.metrics import REGISTRY  # noqa: PLC0415

    def sample(metric: str) -> float:
        return REGISTRY.get_sample_value(metric, {"cache": "settings"}) or 0.0

    cache = named_cache("settings")
    key = CacheKey.for_workspace(WORKSPACE, "settings", "counter.probe")
    hits, misses = sample("tumnis_cache_hits_total"), sample("tumnis_cache_misses_total")
    with use_backend(InProcessCache(clock)):
        assert await cache.get(key) is None
        await cache.set(key, b"x")
        assert await cache.get(key) == b"x"
        assert await cache.get(key) == b"x"

    assert sample("tumnis_cache_misses_total") == misses + 1
    assert sample("tumnis_cache_hits_total") == hits + 2
