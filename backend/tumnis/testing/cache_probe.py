"""A second process for the cache invalidation tests (P0-08, T-P0-08-05, 06, 19).

    python -m tumnis.testing.cache_probe --dsn <libpq URL> --key <cache key>

Starts its own InProcessCache and CacheInvalidationListener on the database, caches the
key, prints `ready`, then polls every 20 ms and prints `gone <ms>` once the key is no
longer cached (ms since `ready`).
"""

import argparse
import asyncio
import time

from tumnis.core.cache import CacheInvalidationListener, CacheKey, InProcessCache
from tumnis.core.clock import SystemClock

POLL_S = 0.02
READY_TIMEOUT_S = 20.0


async def probe(dsn: str, key: CacheKey) -> None:
    cache = InProcessCache(SystemClock())
    listener = CacheInvalidationListener(dsn, cache)
    stop = asyncio.Event()
    task = asyncio.create_task(listener.run(stop))
    await asyncio.wait_for(listener.ready.wait(), READY_TIMEOUT_S)
    await cache.set(key, b"probe")
    print("ready", flush=True)
    started = time.monotonic()
    while await cache.get(key) is not None:  # noqa: ASYNC110  # polling is what it measures
        await asyncio.sleep(POLL_S)
    print(f"gone {round((time.monotonic() - started) * 1000)}", flush=True)
    stop.set()
    await task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.dsn, CacheKey(args.key)))


if __name__ == "__main__":
    main()
