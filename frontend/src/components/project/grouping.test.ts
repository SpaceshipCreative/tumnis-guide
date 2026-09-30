// The Tasks view's grouping rule (P0-24, FR-2.6): every status and label lands in one
// group, done tasks show for seven local days, and the groups partition what is shown.
import fc from "fast-check";
import { expect, test } from "vitest";

import { makeTask } from "../../test/factories";
import {
  DONE_RECENT_DAYS,
  groupOf,
  GROUPS,
  groupTasks,
  localDaysBetween,
  type Group,
  type TaskLite,
} from "./grouping";

const TZ = "America/New_York";
const NOW = new Date("2026-03-09T16:00:00Z"); // Monday 12:00 in New York (EDT)

function task(overrides: Partial<TaskLite>): TaskLite {
  return makeTask({ completed_at: null, label: null, ...overrides });
}

test("[P0-24][FR-2.6] T-P0-24-01 grouping table", () => {
  const cases: [Partial<TaskLite>, Group | null][] = [
    [{ status: "waiting_on_human", label: "human" }, "waiting_on_you"],
    [{ status: "waiting_on_human", label: "ai" }, "waiting_on_you"],
    [{ status: "in_review", label: "ai" }, "waiting_on_you"],
    [{ status: "in_review", label: null }, "waiting_on_you"],
    [{ status: "in_progress", label: "ai" }, "waiting_on_agent"],
    [{ status: "in_progress", label: "human" }, "today"],
    [{ status: "in_progress", label: "hybrid" }, "today"],
    [{ status: "in_progress", label: null }, "today"],
    [{ status: "today", label: "ai" }, "today"],
    [{ status: "today", label: null }, "today"],
    [{ status: "backlog", label: "human" }, "up_next"],
    [{ status: "backlog", label: null }, "up_next"],
    // Done: shown while fewer than 7 local days have passed since completion.
    [{ status: "done", completed_at: "2026-03-09T13:00:00Z" }, "done_recently"],
    [{ status: "done", completed_at: "2026-03-03T15:00:00Z" }, "done_recently"],
    [{ status: "done", completed_at: "2026-03-02T15:00:00Z" }, null],
    // 23:30 on Mar 1 in New York is Mar 2 in UTC: 8 local days, hidden.
    [{ status: "done", completed_at: "2026-03-02T04:30:00Z" }, null],
    // 20:00 on Mar 2 in New York is Mar 3 in UTC: 7 local days, hidden.
    [{ status: "done", completed_at: "2026-03-03T01:00:00Z" }, null],
  ];
  for (const [overrides, expected] of cases) {
    expect(groupOf(task(overrides), NOW, TZ), JSON.stringify(overrides)).toBe(
      expected,
    );
  }
  expect(localDaysBetween("2026-03-08T23:59:00-04:00", NOW, TZ)).toBe(1);
  expect(localDaysBetween("2026-03-09T00:01:00-04:00", NOW, TZ)).toBe(0);
});

const STATUS = fc.constantFrom(
  "backlog",
  "today",
  "in_progress",
  "waiting_on_human",
  "in_review",
  "done",
) satisfies fc.Arbitrary<TaskLite["status"]>;
const LABEL = fc.constantFrom(
  "human",
  "ai",
  "hybrid",
  null,
) satisfies fc.Arbitrary<TaskLite["label"]>;
const THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000;

test("[P0-24][FR-2.6] T-P0-24-02 groups partition the visible tasks", () => {
  fc.assert(
    fc.property(
      fc.array(
        fc.record({
          status: STATUS,
          label: LABEL,
          ago: fc.integer({ min: 0, max: THIRTY_DAYS_MS }),
        }),
        { maxLength: 30 },
      ),
      (specs) => {
        const tasks = specs.map(({ status, label, ago }) =>
          task({
            status,
            label,
            completed_at:
              status === "done"
                ? new Date(NOW.getTime() - ago).toISOString()
                : null,
          }),
        );
        const groups = groupTasks(tasks, NOW, TZ);
        const shown = GROUPS.flatMap((g) => groups[g].map((t) => t.id));
        // Disjoint: no task in two groups.
        expect(new Set(shown).size).toBe(shown.length);
        // Union plus hidden is the input; hidden only when done 7 or more days ago.
        const hidden = tasks.filter((t) => !shown.includes(t.id));
        expect(shown.length + hidden.length).toBe(tasks.length);
        for (const t of hidden) {
          expect(t.status).toBe("done");
          expect(
            localDaysBetween(t.completed_at ?? "", NOW, TZ),
          ).toBeGreaterThanOrEqual(DONE_RECENT_DAYS);
        }
        for (const g of GROUPS) {
          for (const t of groups[g]) expect(groupOf(t, NOW, TZ)).toBe(g);
        }
      },
    ),
  );
});
