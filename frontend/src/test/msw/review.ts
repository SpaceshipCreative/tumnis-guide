// MSW handlers for the review queue (P1-13): `GET /v1/review` answers a queue that loses
// each item a decision closes (a snooze hides it too), `POST /v1/review/{id}/decide`
// records what was sent, and `GET /v1/review/count` answers a count the test can change.
// Every fixture is synthetic.
import { http, HttpResponse, type RequestHandler } from "msw";

import { Recorder } from "./settings";

export interface ReviewItemFixture {
  id: string;
  kind: string;
  project_id: string | null;
  target_type: string;
  target_id: string;
  target_title: string | null;
  payload: Record<string, unknown>;
  blocking_impact: number;
  jev_factor: number;
  actions: string[];
  primary_action: string;
  snoozed_until: string | null;
  decided_at: string | null;
  decision: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

const PRIMARY = ["accept", "approve", "answer"];
const AT = "2026-03-09T12:00:00Z";

/** A queue item; `actions` default to accept, edit, reject and snooze. */
export function makeReviewItem(
  overrides: Partial<ReviewItemFixture> = {},
): ReviewItemFixture {
  const actions = overrides.actions ?? ["accept", "edit", "reject", "snooze"];
  return {
    id: crypto.randomUUID(),
    kind: "low_confidence_label",
    project_id: crypto.randomUUID(),
    target_type: "task",
    target_id: crypto.randomUUID(),
    target_title: "Draft the welcome email",
    payload: {
      suggested: "hybrid",
      probabilities: { human: 0.2, ai: 0.18, hybrid: 0.62 },
      reason: "Needs your judgment",
      decision_id: crypto.randomUUID(),
    },
    blocking_impact: 1,
    jev_factor: 1,
    actions,
    primary_action:
      PRIMARY.find((a) => actions.includes(a)) ?? actions[0] ?? "accept",
    snoozed_until: null,
    decided_at: null,
    decision: null,
    version: 1,
    created_at: AT,
    updated_at: AT,
    ...overrides,
  };
}

export interface ReviewQueueMock {
  handlers: RequestHandler[];
  recorder: Recorder;
  /** The search params of every `GET /v1/review`. */
  reads: URLSearchParams[];
  /** Sets what `GET /v1/review/count` answers from now on. */
  setCount: (count: number) => void;
}

/** A queue of `items` (in the server's order) and its badge count. */
export function reviewQueue(
  items: readonly ReviewItemFixture[],
  count: number = items.length,
): ReviewQueueMock {
  const recorder = new Recorder();
  const reads: URLSearchParams[] = [];
  let open = [...items];
  let badge = count;
  const handlers = [
    http.get("*/v1/review", ({ request }) => {
      const search = new URL(request.url).searchParams;
      reads.push(search);
      const kind = search.get("kind");
      return HttpResponse.json({
        items: open.filter((item) => kind === null || item.kind === kind),
        next_cursor: null,
      });
    }),
    http.get("*/v1/review/count", () => HttpResponse.json({ count: badge })),
    http.post("*/v1/review/:id/decide", async ({ request, params }) => {
      const sent = await recorder.record(request);
      const body = sent.body as { action: string; snooze_until?: string };
      const item = open.find((row) => row.id === params.id);
      if (item === undefined) {
        return HttpResponse.json(
          { title: "Not found", status: 404, code: "not_found" },
          { status: 404 },
        );
      }
      open = open.filter((row) => row.id !== item.id);
      badge = Math.max(0, badge - 1);
      const snoozed = body.action === "snooze";
      return HttpResponse.json({
        ...item,
        version: item.version + 1,
        decision: snoozed ? null : body.action,
        decided_at: snoozed ? null : AT,
        snoozed_until: snoozed ? (body.snooze_until ?? null) : null,
      });
    }),
  ];
  return {
    handlers,
    recorder,
    reads,
    setCount: (next) => {
      badge = next;
    },
  };
}
