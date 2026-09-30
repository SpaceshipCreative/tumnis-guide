// The app's one offline-queue actor (P0-25, FR-3.10), shared through context:
// `QuickAddHost` provides it; captures go in with `useEnqueueTask`; task views read what
// is still waiting with `usePendingTasks`. Outside the host (a component rendered on its
// own) the context holds an actor that is never started, so the hooks read an empty queue.
import { useSelector } from "@xstate/react";
import { createContext, useCallback, useContext } from "react";
import { type ActorRefFrom, createActor } from "xstate";

import type { TaskCreate } from "../../api/types.gen";
import {
  offlineQueueMachine,
  type QueueItem,
} from "../../machines/offlineQueue";
import type { TaskLite } from "../project/grouping";

export type QueueActor = ActorRefFrom<typeof offlineQueueMachine>;

export const QueueContext = createContext<QueueActor>(
  createActor(offlineQueueMachine, { input: { online: false } }),
);

export function useQueueActor(): QueueActor {
  return useContext(QueueContext);
}

const sameItems = (a: readonly QueueItem[], b: readonly QueueItem[]) =>
  a.length === b.length && a.every((item, i) => item === b[i]);

/** Items still waiting to reach the server, of one project or all. */
export function usePendingTasks(projectId?: string): readonly QueueItem[] {
  return useSelector(
    useQueueActor(),
    (snapshot) =>
      snapshot.context.items.filter(
        (item) => projectId === undefined || item.body.project_id === projectId,
      ),
    sameItems,
  );
}

let lastCreatedAt = 0;

/** A new item: its key is born here and never changes for this body (REL-2). */
export function newQueueItem(body: TaskCreate): QueueItem {
  // Strictly increasing, so two captures in one millisecond keep their order.
  lastCreatedAt = Math.max(Date.now(), lastCreatedAt + 1);
  return {
    idempotencyKey: crypto.randomUUID(),
    kind: "create_task",
    body,
    createdAt: lastCreatedAt,
    attempts: 0,
  };
}

/** Puts a new task on the queue; online it goes at once, offline when back. */
export function useEnqueueTask(): (body: TaskCreate) => QueueItem {
  const actor = useQueueActor();
  return useCallback(
    (body) => {
      const item = newQueueItem(body);
      actor.send({ type: "ENQUEUE", item });
      return item;
    },
    [actor],
  );
}

const PENDING_PREFIX = "pending:";

export function isPendingId(id: string): boolean {
  return id.startsWith(PENDING_PREFIX);
}

/** The idempotency key behind a pending row's id. */
export function pendingKey(id: string): string {
  return id.slice(PENDING_PREFIX.length);
}

/** A waiting item as a Tasks-view row: Backlog, label pending (R-08), until it syncs. */
export function pendingRow(item: QueueItem): TaskLite {
  return {
    id: `${PENDING_PREFIX}${item.idempotencyKey}`,
    project_id: item.body.project_id,
    parent_id: null,
    title: item.body.title,
    status: "backlog",
    label: null,
    priority: "normal",
    due_on: null,
    estimate_minutes: null,
    completed_at: null,
    version: 0,
  };
}
