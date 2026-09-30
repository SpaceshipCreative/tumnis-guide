// A route loader's read (P0-25, FR-3.10). The router waits for its loader, and a read that
// pauses while the device is offline would leave the page blank. This one is never paused
// and tries once, so an offline open settles at once and the page shows what it has (the
// captures waiting on the queue) instead of waiting for the network.
import type {
  QueryClient,
  QueryExecuteOptions,
  QueryKey,
} from "@tanstack/react-query";

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
    .query({ ...read, networkMode: "always", retry: false })
    .catch(() => undefined);
}
