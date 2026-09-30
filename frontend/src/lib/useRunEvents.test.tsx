// useRunEvents (P2-04): an ended run whose log is longer than one fetch reads (MAX_PAGES
// pages) still ends up with every event; polling goes on until a short page arrives.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { server } from "../test/msw/server";
import {
  RUN_EVENTS_PAGE,
  RUN_EVENTS_POLL_MS,
  useRunEvents,
} from "./useRunEvents";

const RUN_ID = "01950000-0000-7000-8000-000000000911";
const TOTAL = 20 * RUN_EVENTS_PAGE + 50; // more than one fetch's 20 pages

afterEach(() => {
  vi.useRealTimers();
});

test("[P2-04][FR-5.5] an ended run's long log is read to its end", async () => {
  vi.useFakeTimers({
    shouldAdvanceTime: true,
    toFake: ["setInterval", "setTimeout"],
  });
  server.use(
    http.get("*/v1/runs/:id/events", ({ request }) => {
      const url = new URL(request.url);
      const after = Number(url.searchParams.get("after_seq") ?? "0");
      const limit = Number(url.searchParams.get("limit") ?? "100");
      const last = Math.min(after + limit, TOTAL);
      const items = [];
      for (let seq = after + 1; seq <= last; seq += 1) {
        items.push({
          seq,
          message_id: `01950000-0000-7000-8000-${String(seq).padStart(12, "0")}`,
          kind: "log",
          payload: { seq, kind: "log", text: `line ${String(seq)}` },
          at: "2026-03-09T12:00:00Z",
        });
      }
      return HttpResponse.json({ items, next_after_seq: last });
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );

  const { result } = renderHook(() => useRunEvents(RUN_ID, { active: false }), {
    wrapper,
  });

  await waitFor(() => {
    expect(result.current.data?.length).toBe(20 * RUN_EVENTS_PAGE);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(RUN_EVENTS_POLL_MS + 100);
  });
  await waitFor(() => {
    expect(result.current.data?.length).toBe(TOTAL);
  });
  expect(result.current.data?.at(-1)?.seq).toBe(TOTAL);
});
