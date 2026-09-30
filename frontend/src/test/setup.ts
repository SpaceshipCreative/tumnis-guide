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

// MSW 3 names the option `onUnhandledFrame` (MSW 2: `onUnhandledRequest`).
// "error" makes any request without a handler reject.
beforeAll(() => {
  server.listen({ onUnhandledFrame: "error" });
});
// Route loads still running finish first, against this test's handlers (routers.ts).
afterEach(async () => {
  await settleRouters();
  server.resetHandlers();
  cleanup();
  // A fresh IndexedDB per test: the offline queue persists there (P0-25).
  await resetIdb();
});
afterAll(() => {
  server.close();
});
