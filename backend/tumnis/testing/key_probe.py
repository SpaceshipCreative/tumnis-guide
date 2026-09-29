"""A second process for the key revocation test (P0-14, T-P0-14-04).

    python -m tumnis.testing.key_probe --database-url <url> --pepper-file <path> --key <key>

Starts its own cache backend and invalidation listener on the database (as the api does),
authenticates the key through `authenticate_bearer` (which caches the lookup), prints
`ok`, then authenticates again every 20 ms and prints `refused <ms>` (ms since `ok`) once
the key is refused.
"""

import argparse
import asyncio
import time

from tumnis.core import cache, crypto, db
from tumnis.core.cache import CacheInvalidationListener, InProcessCache
from tumnis.core.clock import SystemClock
from tumnis.core.principal import Principal
from tumnis.modules.auth import api

POLL_S = 0.02
READY_TIMEOUT_S = 20.0


async def probe(database_url: str, pepper_file: str, key: str) -> None:
    clock = SystemClock()
    crypto.configure_peppers(
        lambda: crypto.load_master_keys(pepper_file, strict_owner=False, what=crypto.PEPPER_FILE)
    )
    db.configure(database_url, database_url, pooled=False)
    local = InProcessCache(clock)
    cache.configure_backend(local)
    listener = CacheInvalidationListener(database_url, local)
    stop = asyncio.Event()
    task = asyncio.create_task(listener.run(stop))
    await asyncio.wait_for(listener.ready.wait(), READY_TIMEOUT_S)
    if not isinstance(await api.authenticate_bearer(key, now=clock.now()), Principal):
        print("refused 0", flush=True)
        return
    print("ok", flush=True)
    started = time.monotonic()
    while isinstance(await api.authenticate_bearer(key, now=clock.now()), Principal):  # noqa: ASYNC110  # polling is what it measures
        await asyncio.sleep(POLL_S)
    print(f"refused {round((time.monotonic() - started) * 1000)}", flush=True)
    stop.set()
    await task
    await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--pepper-file", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.database_url, args.pepper_file, args.key))


if __name__ == "__main__":
    main()
