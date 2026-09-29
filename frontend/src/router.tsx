// The router (P0-22). Spec stub.
import type { QueryClient } from "@tanstack/react-query";
import type { AnyRouter, RouterHistory } from "@tanstack/react-router";

export function createAppRouter(opts: {
  queryClient: QueryClient;
  history?: RouterHistory;
}): AnyRouter {
  throw new Error(
    `createAppRouter ${String(opts.history?.location.pathname)}: not implemented (spec:P0-22)`,
  );
}
