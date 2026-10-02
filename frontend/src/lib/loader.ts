// A route loader's read (P0-25, FR-3.10). The router waits for its loader, and a read that
// pauses while the device is offline would leave the page blank. This one is never paused
// and tries once, so an offline open settles at once and the page shows what it has (the
// captures waiting on the queue) instead of waiting for the network. The one exception is
// a 429, which is an answer, not the network: it is asked again after its Retry-After, at
// most twice (lib/retry.ts), so a reload that ran into the rate limit does not keep the
// refusal in the cache (J1 A1.2).
import type {
  QueryClient,
  QueryExecuteOptions,
  QueryKey,
} from "@tanstack/react-query";

import { retryDelay, retryRateLimited } from "./retry";

/** Runs `read` for a loader; a failure is left to the part of the page that shows it. */
export function loaderRead<
  TQueryFnData,
  TError,
  TData,
  TQueryData,
  TQueryKey extends QueryKey,
>(
  client: QueryClient,
  read: QueryExecuteOptions<TQueryFnData, TError, TData, TQueryData, TQueryKey>,
): Promise<unknown> {
  return client
    .query({
      ...read,
      networkMode: "always",
      retry: retryRateLimited,
      retryDelay,
    })
    .catch(() => undefined);
}
