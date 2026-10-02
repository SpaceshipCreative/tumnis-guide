// A burst of live messages refetches each query once (P0-22, ADR-0004). Each message used
// to start its own refetch, cancelling the one before: a quick-add's few `review_item`
// and `task` messages sent GET /v1/review/count several times within 30 ms, and an e2e
// run 18 times in 5 s, eating into the session's rate-limit burst.
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { FakeSocket } from "../test/fakeSocket";
import { connectLive } from "./ws";

beforeEach(() => {
  FakeSocket.reset();
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.useRealTimers();
});

const REVIEW_COUNT = [
  { _id: "tasksGetReviewCount", baseUrl: "http://localhost:3000" },
];

test("[P0-22][ADR-0004] a burst of live messages refetches a shown query once", async () => {
  vi.useFakeTimers();
  const queryClient = new QueryClient();
  const fetches = vi.fn(() => Promise.resolve({ count: 1 }));
  const observer = new QueryObserver(queryClient, {
    queryKey: REVIEW_COUNT,
    queryFn: fetches,
  });
  const unsubscribe = observer.subscribe(() => undefined);
  await vi.runAllTimersAsync();
  expect(fetches).toHaveBeenCalledTimes(1); // the first read

  const stop = connectLive(queryClient, "ws://localhost:3000/ws");
  FakeSocket.latest().open();
  for (let i = 0; i < 5; i += 1) {
    FakeSocket.latest().receive({
      entity: "review_item",
      id: `0193e5a0-0000-7000-8000-00000000000${String(i)}`,
    });
  }
  // Marked stale at once, as before (T-P0-22-09)...
  expect(
    queryClient.getQueryCache().find({ queryKey: REVIEW_COUNT })?.state
      .isInvalidated,
  ).toBe(true);
  await vi.runAllTimersAsync();
  // ...and read again once for the whole burst.
  expect(fetches).toHaveBeenCalledTimes(2);

  // A message after the burst's refetch reads it again.
  FakeSocket.latest().receive({
    entity: "review_item",
    id: "0193e5a0-0000-7000-8000-000000000009",
  });
  await vi.runAllTimersAsync();
  expect(fetches).toHaveBeenCalledTimes(3);

  stop();
  unsubscribe();
  queryClient.clear();
});
