// A burst of live messages refetches each query once (P0-22, ADR-0004). Each message used
// to start its own refetch, cancelling the one before: the `review_item` messages after
// one quick-add sent GET /v1/review/count ten times in a second in an e2e run, eating
// into the session's rate-limit burst.
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

const message = (n: number) => ({
  entity: "review_item",
  id: `0193e5a0-0000-7000-8000-0000000000${String(n).padStart(2, "0")}`,
});

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
  // The first message in a quiet moment is read at once...
  FakeSocket.latest().receive(message(0));
  await vi.advanceTimersByTimeAsync(0);
  expect(fetches).toHaveBeenCalledTimes(2);

  // ...and the rest of the burst marks the query stale (T-P0-22-09) but waits.
  for (let i = 1; i < 10; i += 1) FakeSocket.latest().receive(message(i));
  expect(
    queryClient.getQueryCache().find({ queryKey: REVIEW_COUNT })?.state
      .isInvalidated,
  ).toBe(true);
  await vi.advanceTimersByTimeAsync(0);
  expect(fetches).toHaveBeenCalledTimes(2);

  // One read for the rest of the burst, and none once it is quiet.
  await vi.runAllTimersAsync();
  expect(fetches).toHaveBeenCalledTimes(3);

  // A message after a quiet moment is read at once again.
  FakeSocket.latest().receive(message(20));
  await vi.advanceTimersByTimeAsync(0);
  expect(fetches).toHaveBeenCalledTimes(4);

  stop();
  unsubscribe();
  queryClient.clear();
});
