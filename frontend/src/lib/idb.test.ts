// The offline queue's store (P0-25, FR-3.10): a write IndexedDB refuses (quota, a value
// it cannot clone) keeps the item in this page's memory, so it is still listed, still
// counts as queued for `sendQueued`, and can be deleted.
import { expect, test } from "vitest";

import type { QueueItem } from "../machines/offlineQueue";
import { idbQueue } from "./idb";

function queueItem(title: string, createdAt: number): QueueItem {
  return {
    idempotencyKey: crypto.randomUUID(),
    kind: "create_task",
    body: { project_id: "01890000-0000-7000-8000-000000000001", title },
    createdAt,
    attempts: 0,
  };
}

test("an item IndexedDB refuses to store stays queued in memory", async () => {
  const stored = queueItem("Stored", 1);
  const refused = queueItem("Refused", 2);
  // A function cannot be cloned, so IndexedDB rejects this put.
  const unclonable = {
    ...refused,
    body: { ...refused.body, extra: () => undefined },
  } as unknown as QueueItem;

  await idbQueue.put(stored);
  await idbQueue.put(unclonable);

  expect(await idbQueue.has(refused.idempotencyKey)).toBe(true);
  expect((await idbQueue.all()).map((i) => i.body.title)).toEqual([
    "Stored",
    "Refused",
  ]);

  await idbQueue.delete(refused.idempotencyKey);
  expect(await idbQueue.has(refused.idempotencyKey)).toBe(false);
  expect((await idbQueue.all()).map((i) => i.body.title)).toEqual(["Stored"]);
});
