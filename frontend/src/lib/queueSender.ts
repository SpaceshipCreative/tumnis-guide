// Sends one queued item (P0-25, FR-3.10, REL-2) and maps the answer to what the queue
// does next: ok (drop it), conflict (a 4xx the same request will get again: wait for the
// user) or retry (401, 408, 429, 5xx, no network: back off, same key). One tab replays
// at a time (Web Locks, https://developer.mozilla.org/en-US/docs/Web/API/Web_Locks_API);
// inside the lock an item another tab already sent is skipped, and a sent item leaves
// IndexedDB before the lock is let go, so the next tab sees it gone.
import type { QueryClient } from "@tanstack/react-query";

import type { TaskOut } from "../api/types.gen";
import { zTaskOut } from "../api/zod.gen";
import type { QueueItem, SendResult } from "../machines/offlineQueue";
import { ApiError, apiWrite } from "./fetch";
import { idbQueue } from "./idb";
import { invalidateTaskViews } from "./task-cache";

export const QUEUE_LOCK = "tumnis-offline-queue";
const CONFLICT_STATUSES = new Set([400, 403, 404, 409, 422]);

interface SenderHooks {
  /** Task views refetch before the pending row goes, so the task never blinks out. */
  queryClient?: QueryClient;
  /** Each task the server created (the host keeps it for undo). */
  onCreated?: (task: TaskOut) => void;
}

let hooks: SenderHooks = {};

/** The host hands in the app's QueryClient and what to do with a created task. */
export function setSenderHooks(next: SenderHooks): void {
  hooks = next;
}

async function sendOnce(item: QueueItem): Promise<SendResult> {
  // Another tab sent it (and forgot it) while this one waited for the lock.
  if (!(await idbQueue.has(item.idempotencyKey))) {
    return { outcome: "ok", taskId: "" };
  }
  let task: TaskOut;
  try {
    task = await apiWrite<TaskOut>({
      kind: "create",
      method: "POST",
      path: "/tasks",
      body: item.body,
      idempotencyKey: item.idempotencyKey,
      schema: zTaskOut,
    });
  } catch (error) {
    if (error instanceof ApiError && CONFLICT_STATUSES.has(error.status)) {
      return { outcome: "conflict", problem: error.problem };
    }
    return {
      outcome: "retry",
      problem: error instanceof ApiError ? error.problem : undefined,
    };
  }
  await idbQueue.delete(item.idempotencyKey);
  hooks.onCreated?.(task);
  if (hooks.queryClient) await invalidateTaskViews(hooks.queryClient);
  return { outcome: "ok", taskId: task.id };
}

export function sendQueued(item: QueueItem): Promise<SendResult> {
  const locks = (navigator as Partial<Navigator>).locks;
  return locks
    ? locks.request(QUEUE_LOCK, () => sendOnce(item))
    : sendOnce(item);
}
