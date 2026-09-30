// Routers a test has rendered and not unmounted (render.tsx tracks them), so setup.ts can
// let their loads finish before the test is torn down (issue #76). This module does not
// import the app, so setup.ts can load it for every test file cheaply.
import type { AnyRouter } from "@tanstack/react-router";
import { waitFor } from "@testing-library/react";

// On globalThis, so a copy of render.tsx loaded after `vi.resetModules()` shares it.
const MOUNTED = "__tumnisMountedRouters";

function mountedRouters(): Set<AnyRouter> {
  const store = globalThis as unknown as Record<
    typeof MOUNTED,
    Set<AnyRouter> | undefined
  >;
  return (store[MOUNTED] ??= new Set());
}

/** Tracks a rendered router; call the returned function when it is unmounted. */
export function trackRouter(router: AnyRouter): () => void {
  const mounted = mountedRouters();
  mounted.add(router);
  return () => {
    mounted.delete(router);
  };
}

/**
 * Waits until every tracked router has finished loading. setup.ts runs it after each
 * test, before the test's handlers are reset and its tree is unmounted. A load that
 * outlives its test otherwise commits into an unmounted tree and never finishes, or, once
 * the file is done, after jsdom is gone ("window is not defined").
 */
export async function settleRouters(): Promise<void> {
  const mounted = mountedRouters();
  const routers = [...mounted];
  mounted.clear();
  await waitFor(() => {
    for (const router of routers) {
      if (router.state.status !== "idle") {
        throw new Error(`${router.state.location.href} is still loading`);
      }
    }
  });
}
