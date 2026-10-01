// The app-open ping (P1-18, PRD Success metrics: daily open rate). Once per app start, and
// again when the app comes back after at least 30 minutes hidden, the PWA tells the server
// it was opened (`POST /v1/metrics/open`, 204). The server counts open DAYS, so a repeat
// costs nothing; a failed ping (offline, signed out) is dropped, never retried or shown.
import { useEffect, useRef } from "react";

import { apiWrite } from "../../lib/fetch";

/** Hidden this long counts as a new open (plan default). */
export const REOPEN_AFTER_MS = 30 * 60 * 1000;

function ping(): void {
  apiWrite<undefined>({
    kind: "create",
    method: "POST",
    path: "/metrics/open",
    idempotencyKey: crypto.randomUUID(),
  }).catch(() => undefined);
}

/** Sends the ping while `enabled` (signed in); `now` is injectable for tests. */
export function useAppOpenPing(
  enabled: boolean,
  now: () => number = Date.now,
): void {
  const sent = useRef(false);
  const hiddenAt = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    if (!sent.current) {
      sent.current = true;
      ping();
    }
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        hiddenAt.current = now();
        return;
      }
      const since = hiddenAt.current;
      hiddenAt.current = null;
      if (since !== null && now() - since >= REOPEN_AFTER_MS) ping();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, now]);
}
