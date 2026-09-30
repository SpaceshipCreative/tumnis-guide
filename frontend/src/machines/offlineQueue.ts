// The offline queue (P0-25, FR-3.10, REL-2). Every capture, online or not, goes through
// it: an item is born with its idempotency key, persisted to IndexedDB, and sent head
// first; a transient failure backs off and keeps the key (the server replays the stored
// response for 24 hours), a 4xx waits in `conflict` for Retry, Discard or Edit (a new
// body takes a new key). Every guard, action, actor and delay the config names is
// declared in `setup` (R-38; machines.test.ts sweeps it).
import { assign, enqueueActions, fromPromise, setup } from "xstate";

import type { Problem, TaskCreate } from "../api/types.gen";
import { idbQueue } from "../lib/idb";
import { sendQueued } from "../lib/queueSender";

export interface QueueItem {
  idempotencyKey: string;
  kind: "create_task";
  body: TaskCreate;
  createdAt: number;
  attempts: number;
  problem?: Problem | undefined;
}

export type SendResult =
  | { outcome: "ok"; taskId: string }
  | { outcome: "conflict"; problem: Problem }
  | { outcome: "retry"; problem?: Problem | undefined };

export interface QueueContext {
  items: QueueItem[];
  online: boolean;
}

export type QueueEvent =
  | { type: "ENQUEUE"; item: QueueItem }
  | { type: "ONLINE" }
  | { type: "OFFLINE" }
  | { type: "RETRY" }
  | { type: "DISCARD" }
  | { type: "EDIT"; body: TaskCreate };

export const BACKOFF_CAP_MS = 30_000; // (plan default)
export const SYNCED_SHOW_MS = 1_500; // (plan default) "Synced" shows briefly

/** 0 for a fresh item, then 2 s, 4 s, 8 s … up to the cap. */
export function backoffMs(attempts: number): number {
  return attempts === 0 ? 0 : Math.min(BACKOFF_CAP_MS, 1_000 * 2 ** attempts);
}

/** The queue's head with `change` applied; the rest untouched. */
function withHead(
  items: readonly QueueItem[],
  change: (head: QueueItem) => QueueItem,
): QueueItem[] {
  const [head, ...rest] = items;
  return head ? [change(head), ...rest] : [...items];
}

export const offlineQueueMachine = setup({
  types: {
    context: {} as QueueContext,
    events: {} as QueueEvent,
    input: {} as { online: boolean },
  },
  actors: {
    loadItems: fromPromise<QueueItem[]>(() => idbQueue.all()),
    sendHead: fromPromise<SendResult, QueueItem>(({ input }) =>
      sendQueued(input),
    ),
  },
  actions: {
    append: assign({
      items: ({ context, event }) =>
        event.type === "ENQUEUE"
          ? [...context.items, event.item]
          : context.items,
    }),
    persistNew: (_, params: { item: QueueItem }) => {
      void idbQueue.put(params.item);
    },
    setItems: assign({
      items: (_, params: { items: QueueItem[] }) => params.items,
    }),
    markOnline: assign({ online: true }),
    markOffline: assign({ online: false }),
    dropHead: assign({ items: ({ context }) => context.items.slice(1) }),
    forgetHead: ({ context }) => {
      const head = context.items[0];
      if (head) void idbQueue.delete(head.idempotencyKey);
    },
    bumpAttempts: assign({
      items: ({ context }) =>
        withHead(context.items, (h) => ({ ...h, attempts: h.attempts + 1 })),
    }),
    recordProblem: assign({
      items: ({ context }, params: { problem: Problem }) =>
        withHead(context.items, (h) => ({ ...h, problem: params.problem })),
    }),
    resetHead: assign({
      items: ({ context }) =>
        withHead(context.items, (h) => ({
          ...h,
          attempts: 0,
          problem: undefined,
        })),
    }),
    // EDIT: a new body needs a new idempotency key (the server rejects the old key
    // with a new body). The key is made once; the context and IndexedDB both use it.
    // (Explicit type arguments: inside `setup` TypeScript cannot infer them.)
    replaceHeadWithNewKey: enqueueActions<
      QueueContext,
      QueueEvent,
      undefined,
      QueueEvent,
      never,
      never,
      never,
      never,
      never
    >(({ context, event, enqueue }) => {
      const old = context.items[0];
      if (event.type !== "EDIT" || !old) return;
      const next: QueueItem = {
        ...old,
        body: event.body,
        idempotencyKey: crypto.randomUUID(),
        attempts: 0,
        problem: undefined,
      };
      enqueue.assign({ items: [next, ...context.items.slice(1)] });
      enqueue(() => {
        void idbQueue.delete(old.idempotencyKey).then(() => idbQueue.put(next));
      });
    }),
    announceSynced: () => {
      // provided by the host: toast and fresh task lists
    },
  },
  guards: {
    hasItems: ({ context }) => context.items.length > 0,
    isOnline: ({ context }) => context.online,
    sentOk: (_, params: { result: SendResult }) =>
      params.result.outcome === "ok",
    sentConflict: (_, params: { result: SendResult }) =>
      params.result.outcome === "conflict",
  },
  delays: {
    backoff: ({ context }) => backoffMs(context.items[0]?.attempts ?? 0),
  },
}).createMachine({
  id: "offlineQueue",
  context: ({ input }) => ({ items: [], online: input.online }),
  on: {
    ENQUEUE: {
      actions: [
        "append",
        { type: "persistNew", params: ({ event }) => ({ item: event.item }) },
      ],
    },
    ONLINE: { actions: "markOnline" },
    OFFLINE: { actions: "markOffline" },
  },
  initial: "loading",
  states: {
    loading: {
      invoke: {
        src: "loadItems",
        onDone: {
          target: "routing",
          actions: {
            type: "setItems",
            params: ({ event }) => ({ items: event.output }),
          },
        },
        // IndexedDB unavailable: run in memory.
        onError: "idle",
      },
    },
    routing: {
      always: [{ guard: "hasItems", target: "queued" }, { target: "idle" }],
    },
    idle: {
      on: {
        ENQUEUE: {
          target: "queued",
          actions: [
            "append",
            {
              type: "persistNew",
              params: ({ event }) => ({ item: event.item }),
            },
          ],
        },
      },
    },
    queued: {
      on: { ONLINE: { target: "syncing", actions: "markOnline" } },
      after: { backoff: { guard: "isOnline", target: "syncing" } },
    },
    syncing: {
      invoke: {
        src: "sendHead",
        input: ({ context }) => {
          const head = context.items[0];
          if (!head) throw new Error("syncing with an empty queue");
          return head;
        },
        onDone: [
          {
            guard: {
              type: "sentOk",
              params: ({ event }) => ({ result: event.output }),
            },
            target: "afterSend",
            actions: ["forgetHead", "dropHead"],
          },
          {
            guard: {
              type: "sentConflict",
              params: ({ event }) => ({ result: event.output }),
            },
            target: "conflict",
            actions: {
              type: "recordProblem",
              params: ({ event }) => ({
                problem: (event.output as { problem: Problem }).problem,
              }),
            },
          },
          { target: "queued", actions: "bumpAttempts" },
        ],
        // A network failure, or the VPN down.
        onError: { target: "queued", actions: "bumpAttempts" },
      },
    },
    afterSend: {
      always: [{ guard: "hasItems", target: "syncing" }, { target: "done" }],
    },
    done: {
      entry: "announceSynced",
      on: {
        ENQUEUE: {
          target: "queued",
          actions: [
            "append",
            {
              type: "persistNew",
              params: ({ event }) => ({ item: event.item }),
            },
          ],
        },
      },
      after: { [SYNCED_SHOW_MS]: "idle" },
    },
    conflict: {
      on: {
        RETRY: { target: "queued", actions: "resetHead" },
        DISCARD: { target: "routing", actions: ["forgetHead", "dropHead"] },
        EDIT: { target: "queued", actions: "replaceHeadWithNewKey" },
      },
    },
  },
});
