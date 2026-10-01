// MSW handlers for Guardrail (P4-01, FR-10.6): one task's reads (`GET /v1/tasks/{id}` and
// `GET /v1/tasks/{id}/packet`), counted, and its status writes applied to a plan the
// handlers keep. Bodies follow the generated types.
import { http, HttpResponse, type RequestHandler } from "msw";

import type { PlanOut, TaskOut, TaskPacket } from "../../api/types.gen";
import { makeTask } from "../factories";

/** The task's packet (P2-02) with one linked email in its body. */
export function taskPacket(task: TaskOut, email: string): TaskPacket {
  return {
    schema_version: 1,
    kind: "task",
    run_id: "01950000-0000-7000-8000-000000000101",
    profile_id: "01950000-0000-7000-8000-000000000301",
    skill: "task",
    output_schema: { family: "runner", name: "result", version: 1 },
    correlation_id: `task:${task.id}`,
    timeout_s: 600,
    prompt_text: "Use the skill task.",
    tainted: false,
    body: {
      task: { id: task.id, status: task.status },
      context_items: [
        {
          trust: "untrusted",
          tainted: false,
          source: "context_item",
          item: crypto.randomUUID(),
          rendered: `<untrusted-data nonce="u-0123456789abcdef" type="email">${email}</untrusted-data nonce="u-0123456789abcdef">`,
          truncated: false,
          target_type: "email",
          provider_url: null,
        },
      ],
    },
  };
}

/** How often each task read was asked for, by task id. */
export interface ReadCounts {
  task: Map<string, number>;
  packet: Map<string, number>;
}

function bump(counts: Map<string, number>, id: string): void {
  counts.set(id, (counts.get(id) ?? 0) + 1);
}

/**
 * The tasks' reads, counted: each of `tasks` answers `GET /v1/tasks/{id}` (as the plan
 * now has it) and its packet with `emails[id]` linked. `POST /v1/tasks/{id}/status`
 * moves the task (and its plan item) to `to` and answers the task.
 */
export function guardrailTasks(
  plan: { current: PlanOut },
  emails: Record<string, string> = {},
): { counts: ReadCounts; handlers: RequestHandler[] } {
  const counts: ReadCounts = { task: new Map(), packet: new Map() };
  const taskOf = (id: string): TaskOut | undefined => {
    const item = plan.current.items.find((i) => i.task_id === id);
    if (item === undefined) return undefined;
    return makeTask({
      id: item.task_id,
      project_id: item.project_id,
      title: item.title,
      label: item.label as TaskOut["label"],
      estimate_minutes: item.estimate_minutes,
      first_action: item.first_action,
      status: item.status as TaskOut["status"],
      version: 3,
    });
  };
  const handlers: RequestHandler[] = [
    http.get("*/v1/tasks/:taskId/packet", ({ params }) => {
      const task = taskOf(String(params.taskId));
      if (task === undefined) return new HttpResponse(null, { status: 404 });
      bump(counts.packet, task.id);
      return HttpResponse.json(taskPacket(task, emails[task.id] ?? "No email"));
    }),
    http.get("*/v1/tasks/:taskId", ({ params }) => {
      const task = taskOf(String(params.taskId));
      if (task === undefined) return new HttpResponse(null, { status: 404 });
      bump(counts.task, task.id);
      return HttpResponse.json(task);
    }),
    http.post("*/v1/tasks/:taskId/status", async ({ params, request }) => {
      const id = String(params.taskId);
      const { to } = (await request.json()) as { to: string };
      plan.current = {
        ...plan.current,
        items: plan.current.items.map((i) =>
          i.task_id === id ? { ...i, status: to } : i,
        ),
      };
      return HttpResponse.json(taskOf(id));
    }),
  ];
  return { counts, handlers };
}
