// Next-task preparation (P4-01, FR-10.6): `prefetchTaskContext` fills the same queries the
// task's views read (the task, and its packet, which carries the linked context items), so
// the next card renders from the cache; a fresh entry is not asked for again.
import { expect, test } from "vitest";

import {
  agentsGetTaskPacketQueryKey,
  tasksGetTaskQueryKey,
} from "../api/@tanstack/react-query.gen";
import type { TaskPacket } from "../api/types.gen";
import { guardrailTasks } from "../test/msw/guardrail";
import { mondayPlan, planItem } from "../test/msw/planning";
import { server } from "../test/msw/server";
import { createTestQueryClient } from "../test/render";
import { prefetchTaskContext } from "./prefetch";

test.fails(
  "[P4-01][FR-10.6] T-P4-01-10 prefetches next task and its context",
  async () => {
    const next = planItem(2, { title: "Review PR 42", status: "today" });
    const plan = { current: mondayPlan({ items: [next] }) };
    const reads = guardrailTasks(plan, {
      [next.task_id]: "Can you look at PR 42 before Friday?",
    });
    server.use(...reads.handlers);
    const queryClient = createTestQueryClient();

    await prefetchTaskContext(queryClient, next.task_id);

    expect(reads.counts.task.get(next.task_id)).toBe(1);
    expect(reads.counts.packet.get(next.task_id)).toBe(1);
    const path = { path: { task_id: next.task_id } };
    expect(queryClient.getQueryData(tasksGetTaskQueryKey(path))).toMatchObject({
      id: next.task_id,
      title: "Review PR 42",
    });
    const packet = queryClient.getQueryData<TaskPacket>(
      agentsGetTaskPacketQueryKey(path),
    );
    expect(JSON.stringify(packet?.body.context_items)).toContain(
      "Can you look at PR 42 before Friday?",
    );

    // Still fresh: a second preparation asks for nothing.
    await prefetchTaskContext(queryClient, next.task_id);
    expect(reads.counts.task.get(next.task_id)).toBe(1);
    expect(reads.counts.packet.get(next.task_id)).toBe(1);
  },
);
