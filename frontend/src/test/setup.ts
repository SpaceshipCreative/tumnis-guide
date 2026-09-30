import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";

import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { configureClient } from "../lib/client";
import { resetIdb } from "./idb";
import { server } from "./msw/server";
import { settleRouters } from "./routers";

// The generated client, as main.tsx configures it (absolute URLs on jsdom's origin).
configureClient();

// How long `findBy*` and `waitFor` keep retrying. Testing Library's default is 1 s of
// wall-clock time, which a render behind an MSW answer outlasts on a loaded machine
// (issue #76). Retrying longer never passes a check that would fail: a wait still
// ends the moment its check holds, and still fails if it never does. It stays well
// inside vitest.config.ts's testTimeout, so a real failure reports the DOM it saw.
configure({ asyncUtilTimeout: 15_000 });

// Requests the current test made without a handler. The app swallows a failed request
// (TanStack Query keeps it as the query's error), so the "error" strategy alone only
// logs; afterEach fails the test instead, and a test declares every endpoint the page
// asks for (issue #76).
const unhandled: string[] = [];

// The harness's own probe of the "error" strategy (T-P0-02-15) asks loopback on
// purpose; the app only ever talks to jsdom's origin.
const PROBE_HOST = "127.0.0.1";

// MSW 3 names the option `onUnhandledFrame` (MSW 2: `onUnhandledRequest`).
// "error" makes any request without a handler reject.
beforeAll(() => {
  server.events.on("request:unhandled", ({ request }) => {
    const url = new URL(request.url);
    if (url.hostname === PROBE_HOST) return;
    unhandled.push(`${request.method} ${url.pathname}${url.search}`);
  });
  server.listen({ onUnhandledFrame: "error" });
});
// Route loads still running finish first, against this test's handlers (routers.ts).
afterEach(async () => {
  await settleRouters();
  server.resetHandlers();
  cleanup();
  // A fresh IndexedDB per test: the offline queue persists there (P0-25).
  await resetIdb();
  const missed = unhandled.splice(0);
  if (missed.length > 0) {
    throw new Error(
      `Requests without an MSW handler (add them with server.use): ${[...new Set(missed)].join(", ")}`,
    );
  }
});
afterAll(() => {
  server.close();
});
