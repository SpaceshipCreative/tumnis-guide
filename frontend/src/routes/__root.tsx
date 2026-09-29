// The root route (P0-22): the app shell, the session check and the not-found fallback.
import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  Navigate,
  redirect,
} from "@tanstack/react-router";

import { AppShell } from "../components/common/AppShell";
import { PUBLIC_PATHS, sessionProbeOptions } from "../lib/session";

export interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  beforeLoad: async ({ context, location }) => {
    if (PUBLIC_PATHS.has(location.pathname)) return;
    const signedIn = await context.queryClient.query(sessionProbeOptions());
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect
    if (!signedIn) throw redirect({ to: "/login" });
  },
  component: AppShell,
  notFoundComponent: () => <Navigate to="/" replace />,
});
