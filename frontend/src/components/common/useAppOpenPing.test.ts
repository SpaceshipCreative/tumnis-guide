// The app-open ping (P1-18): once per app start, again after 30 minutes hidden, never
// while signed out.
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { REOPEN_AFTER_MS, useAppOpenPing } from "./useAppOpenPing";

let visibility: DocumentVisibilityState = "visible";
Object.defineProperty(document, "visibilityState", {
  configurable: true,
  get: () => visibility,
});

function setVisibility(state: DocumentVisibilityState): void {
  visibility = state;
  document.dispatchEvent(new Event("visibilitychange"));
}

afterEach(() => {
  visibility = "visible";
});

function countPings(): { count: () => number } {
  let pings = 0;
  server.use(
    http.post("*/v1/metrics/open", () => {
      pings += 1;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return { count: () => pings };
}

test("[P1-18] pings once per start and again after 30 minutes hidden", async () => {
  const pings = countPings();
  let clock = 1_000_000;
  const now = () => clock;
  const { rerender } = renderHook(() => {
    useAppOpenPing(true, now);
  });
  await waitFor(() => {
    expect(pings.count()).toBe(1);
  });
  rerender();

  // A short absence is the same visit.
  setVisibility("hidden");
  clock += REOPEN_AFTER_MS - 1;
  setVisibility("visible");

  // Half an hour away is a new open.
  setVisibility("hidden");
  clock += REOPEN_AFTER_MS;
  setVisibility("visible");
  await waitFor(() => {
    expect(pings.count()).toBe(2);
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(pings.count()).toBe(2);
});

test("[P1-18] no ping while signed out", async () => {
  const pings = countPings();
  renderHook(() => {
    useAppOpenPing(false);
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(pings.count()).toBe(0);
});
