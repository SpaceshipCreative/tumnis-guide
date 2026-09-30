// The offline queue (P0-25, FR-3.10, REL-2). Arrives with its implementation.
import { fromPromise, setup } from "xstate";

import type { Problem, TaskCreate } from "../api/types.gen";

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

export const offlineQueueMachine = setup({
  types: {
    context: {} as QueueContext,
    events: {} as QueueEvent,
    input: {} as { online: boolean },
  },
  actors: {
    loadItems: fromPromise<QueueItem[]>(() => Promise.resolve([])),
    sendHead: fromPromise<SendResult, QueueItem>(() =>
      Promise.reject(new Error("the offline queue arrives with P0-25")),
    ),
  },
}).createMachine({
  id: "offlineQueue",
  context: ({ input }) => ({ items: [], online: input.online }),
  initial: "loading",
  states: { loading: {} },
});
