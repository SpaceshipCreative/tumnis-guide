// Is anyone signed in? (P0-13, P0-22) The root route asks before any page but /login and
// /setup; a 401 sends the visitor to /login. Offline counts as signed in, so the shell
// still opens from the service worker.
import { queryOptions } from "@tanstack/react-query";

import { apiUrl } from "./fetch";

export const PUBLIC_PATHS: ReadonlySet<string> = new Set(["/login", "/setup"]);

export function sessionProbeOptions() {
  return queryOptions({
    queryKey: ["session-probe"],
    queryFn: async (): Promise<boolean> => {
      try {
        const response = await fetch(apiUrl("/auth/sessions?limit=1"), {
          credentials: "same-origin",
        });
        return response.status !== 401;
      } catch {
        return true;
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
    // Never paused while offline (P0-25): the query answers for itself when it cannot
    // reach the server, so an offline open still gets the shell.
    networkMode: "always",
  });
}
