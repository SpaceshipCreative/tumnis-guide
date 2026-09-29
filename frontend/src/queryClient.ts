// The app's QueryClient (P0-22). Data goes stale when the live socket says so, not on a
// timer (ADR-0004): a long staleTime, no refetch on focus, one retry for reads.
import { QueryClient } from "@tanstack/react-query";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  });
}
