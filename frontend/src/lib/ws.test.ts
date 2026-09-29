// Live invalidation (P0-22, ADR-0004): a `{entity, id}` message marks stale exactly the
// cached queries LIVE_MAP names for it; a reconnect catches up by invalidating all.
import { QueryClient } from "@tanstack/react-query";
import fc from "fast-check";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import * as generated from "../api/@tanstack/react-query.gen";
import { FakeSocket } from "../test/fakeSocket";
import { LIVE_MAP, type LiveEntity } from "./live-map";
import { connectLive } from "./ws";

const ENTITIES = Object.keys(LIVE_MAP) as LiveEntity[];
const IDS = [
  "0193e5a0-0000-7000-8000-000000000001",
  "0193e5a0-0000-7000-8000-000000000002",
  "0193e5a0-0000-7000-8000-000000000003",
];
// Generated query ops, every LIVE_MAP name, the plan's names and one unknown op.
const OPS = [
  ...new Set([
    ...Object.keys(generated)
      .filter((n) => n.endsWith("QueryKey") && !n.endsWith("InfiniteQueryKey"))
      .map((n) => n.slice(0, -"QueryKey".length)),
    ...Object.values(LIVE_MAP).flatMap((m) => [...m.details, ...m.lists]),
    "tasksGetTask",
    "tasksListTasks",
    "projectsGetProject",
    "settingsGetWorkspace",
    "notAnOperation",
  ]),
];

interface Head {
  _id: string;
  baseUrl: string;
  path?: { id: string };
  _infinite?: boolean;
}

function keyOf(op: string, id: string | null, infinite: boolean): [Head] {
  return [
    {
      _id: op,
      baseUrl: "http://localhost:3000",
      ...(id === null ? {} : { path: { id } }),
      ...(infinite ? { _infinite: true } : {}),
    },
  ];
}

// The reference selection, straight from LIVE_MAP.
function selects(head: Head, entity: string, id: string): boolean {
  if (!(entity in LIVE_MAP)) return false;
  const entry = LIVE_MAP[entity as LiveEntity];
  if (entry.lists.includes(head._id)) return true;
  return entry.details.includes(head._id) && head.path?.id === id;
}

beforeEach(() => {
  FakeSocket.reset();
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.useRealTimers();
});

test("[P0-22][ADR-0004] T-P0-22-09 a live message invalidates exactly the matching queries", () => {
  const entry = fc.record({
    op: fc.constantFrom(...OPS),
    id: fc.option(fc.constantFrom(...IDS), { nil: null }),
    infinite: fc.boolean(),
  });
  const message = fc.record({
    entity: fc.constantFrom(...ENTITIES, "bogus"),
    id: fc.constantFrom(...IDS),
  });
  fc.assert(
    fc.property(
      fc.uniqueArray(entry, {
        selector: (e) => `${e.op}|${e.id ?? ""}|${String(e.infinite)}`,
        maxLength: 25,
      }),
      message,
      (entries, msg) => {
        FakeSocket.reset();
        const queryClient = new QueryClient();
        const keys = entries.map((e) => keyOf(e.op, e.id, e.infinite));
        for (const key of keys) queryClient.setQueryData(key, { cached: true });
        const stop = connectLive(queryClient, "ws://localhost:3000/ws");
        FakeSocket.latest().open();

        FakeSocket.latest().receive(msg);

        for (const key of keys) {
          const query = queryClient.getQueryCache().find({ queryKey: key });
          expect(query?.state.isInvalidated, JSON.stringify({ key, msg })).toBe(
            selects(key[0], msg.entity, msg.id),
          );
        }
        stop();
        queryClient.clear();
      },
    ),
    { numRuns: 200 },
  );

  // The named cases: a task message never touches a project, the workspace
  // settings or another task.
  const queryClient = new QueryClient();
  const [mine, other] = IDS as [string, string];
  const untouched = [
    keyOf("projectsGetProject", mine, false),
    keyOf("settingsGetWorkspace", null, false),
    keyOf("settingsGetWorkspaceSettings", null, false),
    keyOf("tasksGetTask", other, false),
  ];
  for (const key of untouched) queryClient.setQueryData(key, {});
  connectLive(queryClient, "ws://localhost:3000/ws");
  FakeSocket.latest().open();
  FakeSocket.latest().receive({ entity: "task", id: mine });
  FakeSocket.latest().receive("not json");
  FakeSocket.latest().receive({ entity: "task" });
  for (const key of untouched) {
    expect(
      queryClient.getQueryCache().find({ queryKey: key })?.state.isInvalidated,
    ).toBe(false);
  }
});

test("[P0-22][ADR-0004] T-P0-22-10 reconnect after a drop invalidates everything once", () => {
  vi.useFakeTimers();
  const queryClient = new QueryClient();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const stop = connectLive(queryClient, "ws://localhost:3000/ws");

  FakeSocket.latest().open();
  expect(invalidate).not.toHaveBeenCalled();

  FakeSocket.latest().drop();
  vi.advanceTimersByTime(200);
  expect(FakeSocket.instances).toHaveLength(1); // backoff: at least 250 ms
  vi.advanceTimersByTime(300);
  expect(FakeSocket.instances).toHaveLength(2);
  expect(invalidate).not.toHaveBeenCalled(); // not before the socket is back

  FakeSocket.latest().open();
  expect(invalidate).toHaveBeenCalledTimes(1);
  expect(invalidate.mock.calls[0]).toEqual([]);

  // A second drop backs off longer and catches up once more.
  FakeSocket.latest().drop();
  vi.advanceTimersByTime(30_000);
  expect(FakeSocket.instances).toHaveLength(3);
  FakeSocket.latest().open();
  expect(invalidate).toHaveBeenCalledTimes(2);

  // Stopped: a drop no longer reconnects.
  stop();
  expect(FakeSocket.latest().closedByClient).toBe(true);
  vi.advanceTimersByTime(60_000);
  expect(FakeSocket.instances).toHaveLength(3);
});
