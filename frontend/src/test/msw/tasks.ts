// `POST /v1/tasks` as the backend answers it (P0-25, REL-2): the first request with an
// `Idempotency-Key` creates the task; a repeat with the same key gets the stored response
// again with `Idempotent-Replayed: true`. `script` answers the next requests with a
// problem or a network failure first; `gate` holds answers back until it resolves.
import { http, HttpResponse, type RequestHandler } from "msw";

import type { TaskOut } from "../../api/types.gen";
import { makeProblem, makeTask } from "../factories";
import { Recorder } from "./settings";

export type Scripted = { status: number; code: string } | "network";

export class TaskCreateFake {
  readonly recorder = new Recorder();
  /** Answers for the next requests, first in first out, before any task is created. */
  readonly script: Scripted[] = [];
  gate: Promise<void> | null = null;
  private readonly stored = new Map<string, TaskOut>();

  /** How many requests carried this key. */
  requests(key: string): number {
    return this.recorder.sent.filter((s) => s.idempotencyKey === key).length;
  }

  /** Every key seen, once each, in first-seen order. */
  keys(): string[] {
    return [...new Set(this.recorder.sent.map((s) => s.idempotencyKey ?? ""))];
  }

  /** The bodies sent, in order (repeats included). */
  bodies(): unknown[] {
    return this.recorder.sent.map((s) => s.body);
  }

  /** The tasks created (one per key). */
  created(): TaskOut[] {
    return [...this.stored.values()];
  }

  get handlers(): RequestHandler[] {
    return [
      http.post("/v1/tasks", async ({ request }) => {
        const sent = await this.recorder.record(request);
        if (this.gate) await this.gate;
        const next = this.script.shift();
        if (next === "network") return HttpResponse.error();
        if (next) {
          return HttpResponse.json(
            makeProblem({
              status: next.status,
              code: next.code,
              title: next.code,
            }),
            {
              status: next.status,
              headers: { "Content-Type": "application/problem+json" },
            },
          );
        }
        const key = sent.idempotencyKey ?? "";
        const replay = this.stored.get(key);
        if (replay) {
          return HttpResponse.json(replay, {
            status: 201,
            headers: { "Idempotent-Replayed": "true" },
          });
        }
        const body = sent.body as { project_id: string; title: string };
        const task = makeTask({
          id: crypto.randomUUID(),
          project_id: body.project_id,
          title: body.title,
          status: "backlog",
          version: 1,
        });
        this.stored.set(key, task);
        return HttpResponse.json(task, { status: 201 });
      }),
    ];
  }
}
