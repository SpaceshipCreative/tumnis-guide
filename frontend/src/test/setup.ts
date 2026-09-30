import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { configureClient } from "../lib/client";
import { resetIdb } from "./idb";
import { server } from "./msw/server";

// The generated client, as main.tsx configures it (absolute URLs on jsdom's origin).
configureClient();

// MSW 3 names the option `onUnhandledFrame` (MSW 2: `onUnhandledRequest`).
// "error" makes any request without a handler reject.
beforeAll(() => {
  server.listen({ onUnhandledFrame: "error" });
});
afterEach(async () => {
  server.resetHandlers();
  cleanup();
  // A fresh IndexedDB per test: the offline queue persists there (P0-25).
  await resetIdb();
});
afterAll(() => {
  server.close();
});
