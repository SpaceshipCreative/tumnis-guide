// The offline queue (P0-25, FR-3.10, REL-2): every capture goes through one queue that
// persists to IndexedDB (fake-indexeddb here) and replays with its idempotency key.
// Machine tests drive the actor with events; `sendHead` and `loadItems` are provided
// fakes except where the real IndexedDB store and `sendQueued` are the point.
import { afterEach, expect, test, vi } from "vitest";
import { type Actor, createActor, fromPromise, type StateValue } from "xstate";

import type { Problem } from "../api/types.gen";
import { idbQueue } from "../lib/idb";
import { makeProblem } from "../test/factories";
import { locksPolyfill } from "../test/locks";
import { server } from "../test/msw/server";
import { TaskCreateFake } from "../test/msw/tasks";
import {
  backoffMs,
  offlineQueueMachine,
  type QueueItem,
  type SendResult,
} from "./offlineQueue";

const PROJECT = "01890000-0000-7000-8000-000000000001";

let created = 0;
function queueItem(title: string): QueueItem {
  created += 1;
  return {
    idempotencyKey: crypto.randomUUID(),
    kind: "create_task",
    body: { project_id: PROJECT, title },
    createdAt: created,
    attempts: 0,
  };
}

type Machine = typeof offlineQueueMachine;
type Step = SendResult | Error | Promise<SendResult>;

/** A `sendHead` answering `steps` in order (ok once they run out); `calls` holds inputs. */
function scriptedSend(steps: Step[]) {
  const calls: QueueItem[] = [];
  const actor = fromPromise<SendResult, QueueItem>(async ({ input }) => {
    calls.push(input);
    const step = steps.shift() ?? { outcome: "ok", taskId: "t-last" };
    if (step instanceof Error) throw step;
    return step;
  });
  return { calls, actor };
}

function provided(
  stored: QueueItem[],
  send: ReturnType<typeof scriptedSend>,
): Machine {
  return offlineQueueMachine.provide({
    actors: {
      loadItems: fromPromise(() => Promise.resolve(stored)),
      sendHead: send.actor,
    },
  });
}

const running: Actor<Machine>[] = [];

afterEach(() => {
  for (const actor of running.splice(0)) actor.stop();
  vi.useRealTimers();
});

function name(value: StateValue): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** Starts an actor and collects each distinct state it passes through. */
function start(machine: Machine, online: boolean) {
  const actor = createActor(machine, { input: { online } });
  const states: string[] = [];
  const push = (value: StateValue) => {
    if (states.at(-1) !== name(value)) states.push(name(value));
  };
  actor.subscribe((snapshot) => {
    push(snapshot.value);
  });
  actor.start();
  if (states.length === 0) push(actor.getSnapshot().value);
  running.push(actor);
  const state = () => name(actor.getSnapshot().value);
  const items = () => actor.getSnapshot().context.items;
  return { actor, states, state, items };
}

const keysIn = (items: readonly QueueItem[]) =>
  items.map((i) => i.idempotencyKey);

async function storedKeys(): Promise<string[]> {
  return keysIn(await idbQueue.all());
}

test("[P0-25][FR-3.10] T-P0-25-05 online path: idle, queued, syncing, done, idle", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  const send = scriptedSend([{ outcome: "ok", taskId: "t1" }]);
  const { actor, states, items } = start(provided([], send), true);
  await vi.advanceTimersByTimeAsync(0);
  expect(states).toEqual(["loading", "idle"]);

  const item = queueItem("Send logo drafts");
  actor.send({ type: "ENQUEUE", item });
  await vi.advanceTimersByTimeAsync(0);
  await vi.waitFor(() => {
    expect(states).toEqual(["loading", "idle", "queued", "syncing", "done"]);
  });
  expect(send.calls).toEqual([item]);
  expect(items()).toEqual([]);

  // "Synced" shows briefly, then the queue rests.
  await vi.advanceTimersByTimeAsync(1_500);
  expect(states.at(-1)).toBe("idle");
});

test("[P0-25][FR-3.10] T-P0-25-06 offline path waits, then syncs on ONLINE", async () => {
  const send = scriptedSend([]);
  const { actor, state, items } = start(provided([], send), false);
  await vi.waitFor(() => {
    expect(state()).toBe("idle");
  });

  const first = queueItem("Offline one");
  const second = queueItem("Offline two");
  actor.send({ type: "ENQUEUE", item: first });
  actor.send({ type: "ENQUEUE", item: second });
  expect(state()).toBe("queued");
  expect(items()).toEqual([first, second]);
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(state()).toBe("queued");
  expect(send.calls).toEqual([]);

  actor.send({ type: "ONLINE" });
  await vi.waitFor(() => {
    expect(state()).toBe("done");
  });
  expect(keysIn(send.calls)).toEqual(keysIn([first, second]));
  expect(items()).toEqual([]);
});

test("[P0-25][FR-3.10] T-P0-25-07 conflict path: retry, discard, edit with a new key", async () => {
  const problem = makeProblem({
    status: 422,
    code: "validation_error",
    title: "Title is too long",
  }) as Problem;
  const conflict: SendResult = { outcome: "conflict", problem };
  let releaseEdited: (result: SendResult) => void = () => undefined;
  const edited = new Promise<SendResult>((resolve) => {
    releaseEdited = resolve;
  });
  const send = scriptedSend([conflict, conflict, conflict, edited]);
  const { actor, state, items } = start(provided([], send), true);
  await vi.waitFor(() => {
    expect(state()).toBe("idle");
  });

  // The head comes back as a conflict and keeps its problem.
  const first = queueItem("Send logo drafts");
  actor.send({ type: "ENQUEUE", item: first });
  await vi.waitFor(() => {
    expect(state()).toBe("conflict");
  });
  expect(items()[0]).toMatchObject({
    idempotencyKey: first.idempotencyKey,
    problem,
  });

  // RETRY: queued again with its attempts reset, same key.
  actor.send({ type: "RETRY" });
  expect(state()).toBe("queued");
  expect(items()[0]?.attempts).toBe(0);
  expect(items()[0]?.problem).toBeUndefined();
  await vi.waitFor(() => {
    expect(state()).toBe("conflict");
  });
  expect(keysIn(send.calls)).toEqual([
    first.idempotencyKey,
    first.idempotencyKey,
  ]);

  // DISCARD: gone from the context and from IndexedDB.
  await vi.waitFor(async () => {
    expect(await storedKeys()).toEqual([first.idempotencyKey]);
  });
  actor.send({ type: "DISCARD" });
  expect(items()).toEqual([]);
  expect(state()).toBe("idle");
  await vi.waitFor(async () => {
    expect(await storedKeys()).toEqual([]);
  });

  // EDIT: a new body takes a new key; the old key leaves IndexedDB.
  const second = queueItem("Send logo draft");
  actor.send({ type: "ENQUEUE", item: second });
  await vi.waitFor(() => {
    expect(state()).toBe("conflict");
  });
  await vi.waitFor(async () => {
    expect(await storedKeys()).toEqual([second.idempotencyKey]);
  });
  const body = { project_id: PROJECT, title: "Send logo drafts to Acme" };
  actor.send({ type: "EDIT", body });
  expect(state()).toBe("queued");
  const head = items()[0];
  expect(head).toMatchObject({ body, attempts: 0 });
  expect(head?.problem).toBeUndefined();
  expect(head?.idempotencyKey).not.toBe(second.idempotencyKey);
  const newKey = head?.idempotencyKey ?? "";
  await vi.waitFor(() => {
    expect(send.calls).toHaveLength(4);
  });
  expect(send.calls[3]?.idempotencyKey).toBe(newKey);
  await vi.waitFor(async () => {
    expect(await storedKeys()).toEqual([newKey]);
  });

  releaseEdited({ outcome: "ok", taskId: "t2" });
  await vi.waitFor(() => {
    expect(state()).toBe("done");
  });
  await vi.waitFor(async () => {
    expect(await storedKeys()).toEqual([]);
  });
});

test("[P0-25][REL-2] T-P0-25-08 transient failures back off and keep the key", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  const send = scriptedSend([
    new Error("Failed to fetch"),
    new Error("Failed to fetch"),
    { outcome: "ok", taskId: "t1" },
  ]);
  const { actor, state } = start(provided([], send), true);
  await vi.advanceTimersByTimeAsync(0);
  expect(state()).toBe("idle");

  const item = queueItem("Send logo drafts");
  actor.send({ type: "ENQUEUE", item });
  await vi.advanceTimersByTimeAsync(0); // the first send goes at once
  expect(send.calls).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1_999);
  expect(send.calls).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1); // 2 s after the first failure
  expect(send.calls).toHaveLength(2);
  await vi.advanceTimersByTimeAsync(3_999);
  expect(send.calls).toHaveLength(2);
  await vi.advanceTimersByTimeAsync(1); // 4 s after the second
  expect(send.calls).toHaveLength(3);
  await vi.waitFor(() => {
    expect(state()).toBe("done");
  });

  expect(send.calls.map((c) => c.idempotencyKey)).toEqual([
    item.idempotencyKey,
    item.idempotencyKey,
    item.idempotencyKey,
  ]);
  expect(send.calls.map((c) => c.attempts)).toEqual([0, 1, 2]);
});

test("[P0-25][FR-3.10] T-P0-25-09 items persist across a reload", async () => {
  // Tab one, offline: two captures, then the page goes away.
  const before = start(offlineQueueMachine, false);
  await vi.waitFor(() => {
    expect(before.state()).toBe("idle");
  });
  const first = queueItem("Offline one");
  const second = queueItem("Offline two");
  before.actor.send({ type: "ENQUEUE", item: first });
  before.actor.send({ type: "ENQUEUE", item: second });
  await vi.waitFor(async () => {
    expect(await storedKeys()).toHaveLength(2);
  });
  before.actor.stop();

  // The reload: a new actor on the same database finds both, in order.
  const after = start(offlineQueueMachine, false);
  await vi.waitFor(() => {
    expect(after.state()).toBe("queued");
  });
  expect(after.states).toEqual(["loading", "queued"]);
  expect(after.items()).toEqual([first, second]);
});

test("[P0-25][FR-3.10] T-P0-25-10 replay on reconnect and on app open sends each item once", async () => {
  const unlock = locksPolyfill();
  try {
    const fake = new TaskCreateFake();
    server.use(...fake.handlers);
    const stored = [
      queueItem("Stored one"),
      queueItem("Stored two"),
      queueItem("Stored three"),
    ];
    for (const item of stored) await idbQueue.put(item);

    // App open in two tabs at once, both online, over the same three items.
    const tabs = [
      start(offlineQueueMachine, true),
      start(offlineQueueMachine, true),
    ];
    await vi.waitFor(
      async () => {
        expect(await storedKeys()).toEqual([]);
        for (const tab of tabs) {
          expect(["done", "idle"]).toContain(tab.state());
        }
      },
      { timeout: 5_000 },
    );
    expect(fake.keys()).toEqual(keysIn(stored));
    for (const item of stored) {
      expect(fake.requests(item.idempotencyKey)).toBe(1);
    }

    // Reconnect: a capture made offline goes once, when the tab is back online.
    const [tab] = tabs;
    if (!tab) throw new Error("no tab");
    tab.actor.send({ type: "OFFLINE" });
    const offline = queueItem("Captured offline");
    tab.actor.send({ type: "ENQUEUE", item: offline });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(fake.requests(offline.idempotencyKey)).toBe(0);
    tab.actor.send({ type: "ONLINE" });
    await vi.waitFor(async () => {
      expect(await storedKeys()).toEqual([]);
      expect(tab.state()).not.toBe("syncing");
    });
    expect(fake.requests(offline.idempotencyKey)).toBe(1);
    expect(fake.created().map((t) => t.title)).toEqual([
      "Stored one",
      "Stored two",
      "Stored three",
      "Captured offline",
    ]);
  } finally {
    unlock();
  }
});

// The transitions the spec tests above do not reach: a store that cannot load, a capture
// while "Synced" shows, a `retry` answer (not a thrown error), and the backoff cap.
test("[P0-25][FR-3.10] a store that cannot load runs the queue in memory", async () => {
  const send = scriptedSend([]);
  const machine = offlineQueueMachine.provide({
    actors: {
      loadItems: fromPromise<QueueItem[]>(() =>
        Promise.reject(new Error("IndexedDB is blocked")),
      ),
      sendHead: send.actor,
    },
  });
  const { actor, states, state } = start(machine, true);
  await vi.waitFor(() => {
    expect(state()).toBe("idle");
  });
  expect(states).toEqual(["loading", "idle"]);
  actor.send({ type: "ENQUEUE", item: queueItem("In memory") });
  await vi.waitFor(() => {
    expect(state()).toBe("done");
  });
  expect(send.calls).toHaveLength(1);
});

test("[P0-25][FR-3.10] a capture while Synced shows is queued and sent", async () => {
  const send = scriptedSend([]);
  const { actor, state } = start(provided([], send), true);
  await vi.waitFor(() => {
    expect(state()).toBe("idle");
  });
  actor.send({ type: "ENQUEUE", item: queueItem("First") });
  await vi.waitFor(() => {
    expect(state()).toBe("done");
  });
  actor.send({ type: "ENQUEUE", item: queueItem("Second") });
  expect(state()).toBe("queued");
  await vi.waitFor(() => {
    expect(send.calls).toHaveLength(2);
  });
  await vi.waitFor(() => {
    expect(state()).toBe("done");
  });
});

test("[P0-25][REL-2] a retry answer backs off like a failure, up to 30 s", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  const send = scriptedSend([
    { outcome: "retry" },
    { outcome: "retry", problem: makeProblem({ status: 503 }) as Problem },
  ]);
  const { actor, state, items } = start(provided([], send), true);
  await vi.advanceTimersByTimeAsync(0);
  actor.send({ type: "ENQUEUE", item: queueItem("Later") });
  await vi.advanceTimersByTimeAsync(0);
  expect(send.calls).toHaveLength(1);
  expect(state()).toBe("queued");
  expect(items()[0]?.attempts).toBe(1);
  await vi.advanceTimersByTimeAsync(2_000);
  expect(send.calls).toHaveLength(2);
  await vi.advanceTimersByTimeAsync(4_000);
  await vi.waitFor(() => {
    expect(state()).toBe("done");
  });
  expect([0, 1, 2, 4, 5, 10].map(backoffMs)).toEqual([
    0, 2_000, 4_000, 16_000, 30_000, 30_000,
  ]);
});

// Review findings (CodeRabbit on PR 65): two windows in which a capture could be lost.
test("[P0-25][REL-2] an edited item is in IndexedDB before its send starts", async () => {
  const first = queueItem("Edit me");
  await idbQueue.put(first);
  let storedAtSend: boolean | undefined;
  let sends = 0;
  const machine = offlineQueueMachine.provide({
    actors: {
      loadItems: fromPromise(() => Promise.resolve([first])),
      sendHead: fromPromise<SendResult, QueueItem>(async ({ input }) => {
        sends += 1;
        if (sends === 1) {
          return {
            outcome: "conflict",
            problem: makeProblem({ status: 422 }) as Problem,
          };
        }
        // What sendQueued asks first: has another tab sent it already?
        storedAtSend = await idbQueue.has(input.idempotencyKey);
        return { outcome: "ok", taskId: "t" };
      }),
    },
  });
  const { actor, state, items } = start(machine, true);
  await vi.waitFor(() => {
    expect(state()).toBe("conflict");
  });
  actor.send({ type: "EDIT", body: { project_id: PROJECT, title: "Edited" } });
  const edited = items()[0]?.idempotencyKey ?? "";
  await vi.waitFor(() => {
    expect(sends).toBe(2);
  });
  expect(storedAtSend).toBe(true);
  expect(edited).not.toBe(first.idempotencyKey);
  await vi.waitFor(async () => {
    expect(await storedKeys()).not.toContain(first.idempotencyKey);
  });
});

test("[P0-25][FR-3.10] a capture made while the queue loads survives a load that missed it", async () => {
  const send = scriptedSend([]);
  let finishLoad: (items: QueueItem[]) => void = () => undefined;
  const machine = offlineQueueMachine.provide({
    actors: {
      loadItems: fromPromise<QueueItem[]>(
        () =>
          new Promise((resolve) => {
            finishLoad = resolve;
          }),
      ),
      sendHead: send.actor,
    },
  });
  const { actor, state } = start(machine, true);
  expect(state()).toBe("loading");
  const older = queueItem("From last session");
  const early = queueItem("Early");
  actor.send({ type: "ENQUEUE", item: early });
  finishLoad([older]);
  await vi.waitFor(() => {
    expect(send.calls).toHaveLength(2);
  });
  expect(keysIn(send.calls)).toEqual([
    older.idempotencyKey,
    early.idempotencyKey,
  ]);
});
