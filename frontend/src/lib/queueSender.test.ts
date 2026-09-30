// How `sendQueued` reads the server's answer (P0-25, FR-3.10, REL-2): a created task (or a
// replayed one) is ok and leaves IndexedDB; a 4xx the same request would get again is a
// conflict; 401, 408, 429, 5xx and no network are retried with the same key; an item
// another tab already sent is not sent again.
import { expect, test } from "vitest";

import type { QueueItem } from "../machines/offlineQueue";
import { server } from "../test/msw/server";
import { type Scripted, TaskCreateFake } from "../test/msw/tasks";
import { setUnauthorizedHandler } from "./fetch";
import { idbQueue } from "./idb";
import { sendQueued } from "./queueSender";

function queueItem(title: string): QueueItem {
  return {
    idempotencyKey: crypto.randomUUID(),
    kind: "create_task",
    body: { project_id: "01890000-0000-7000-8000-000000000001", title },
    createdAt: Date.now(),
    attempts: 0,
  };
}

test("[P0-25][REL-2] sendQueued maps each answer to ok, conflict or retry", async () => {
  setUnauthorizedHandler(() => undefined);
  const fake = new TaskCreateFake();
  server.use(...fake.handlers);
  const cases: [Scripted | null, string][] = [
    [null, "ok"],
    [{ status: 422, code: "validation_error" }, "conflict"],
    [{ status: 404, code: "not_found" }, "conflict"],
    [{ status: 409, code: "stale_version" }, "conflict"],
    [{ status: 401, code: "unauthenticated" }, "retry"],
    [{ status: 408, code: "request_timeout" }, "retry"],
    [{ status: 429, code: "rate_limited" }, "retry"],
    [{ status: 503, code: "unavailable" }, "retry"],
    ["network", "retry"],
  ];
  for (const [answer, outcome] of cases) {
    const label = JSON.stringify(answer);
    const item = queueItem(`Case ${label}`);
    await idbQueue.put(item);
    if (answer) fake.script.push(answer);
    const result = await sendQueued(item);
    expect(result.outcome, label).toBe(outcome);
    expect(await idbQueue.has(item.idempotencyKey), label).toBe(
      outcome !== "ok",
    );
    if (result.outcome === "conflict" && answer && answer !== "network") {
      expect(result.problem.code, label).toBe(answer.code);
    }
  }
});

test("[P0-25][FR-3.10] an item gone from IndexedDB is not sent again", async () => {
  const fake = new TaskCreateFake();
  server.use(...fake.handlers);
  const item = queueItem("Sent by the other tab");
  expect(await sendQueued(item)).toEqual({ outcome: "ok", taskId: "" });
  expect(fake.recorder.sent).toEqual([]);
});
