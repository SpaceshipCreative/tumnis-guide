// The live socket's lifetime (P0-22, ADR-0004): open while someone is signed in, closed on
// the sign-in and setup pages. `/ws` answers 403 without a session, so a socket opened on
// /login only logged failed upgrades and kept retrying (APP-11).
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { connectLive } from "../../lib/ws";

/** Keeps the live socket open while `enabled` (a signed-in page). */
export function useLiveSocket(enabled: boolean): void {
  const queryClient = useQueryClient();
  useEffect(
    () => (enabled ? connectLive(queryClient) : undefined),
    [enabled, queryClient],
  );
}
