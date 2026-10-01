// Response validation in slices (PERF-2: the board's Core Web Vitals budget). The
// generated validators parse a whole response in one synchronous task; a large one (the
// board, a 200-task page) then blocks the main thread for over 100 ms on a phone. These
// helpers run the same generated schemas over a list's elements a slice at a time and
// give the main thread back between slices, so a long list costs many short tasks.
// The verdict is the generated validator's: the same issues (same paths) in one ZodError.
import * as z from "zod";

/** Elements validated per task: a slice stays well under a 50 ms long task on a phone. */
export const SLICE = 20;

type Issue = z.core.$ZodIssue;

/** Lets the browser run other work (input, paint) before continuing. A new task, not a
 * microtask: `scheduler.yield()` where the browser has it, else a MessageChannel message
 * (unlike `setTimeout`, not clamped and not held back by fake timers in tests). */
export function yieldToMain(): Promise<void> {
  const scheduler = (
    globalThis as { scheduler?: { yield?: () => Promise<void> } }
  ).scheduler;
  if (typeof scheduler?.yield === "function") return scheduler.yield();
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = () => {
      channel.port1.close();
      resolve();
    };
    channel.port2.postMessage(null);
  });
}

/** One value to check: `schema` over `value`, its issues' paths under `path`. */
interface Unit {
  schema: z.ZodType;
  value: unknown;
  path: PropertyKey[];
}

/** Issues of every unit, a slice of units at a time. */
async function issuesInSlices(units: readonly Unit[]): Promise<Issue[]> {
  const issues: Issue[] = [];
  for (const [i, { schema, value, path }] of units.entries()) {
    if (i > 0 && i % SLICE === 0) await yieldToMain();
    const result = schema.safeParse(value);
    if (!result.success) {
      for (const issue of result.error.issues) {
        issues.push({ ...issue, path: [...path, ...issue.path] });
      }
    }
  }
  return issues;
}

function fail(issues: Issue[]): void {
  if (issues.length > 0) throw new z.ZodError(issues);
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * Validates `data` against `page` (an object with an `items` array of `item`) like
 * `page.parseAsync(data)`, with `items` a slice at a time.
 */
export async function parsePageInSlices(
  page: z.ZodType,
  item: z.ZodType,
  data: unknown,
): Promise<void> {
  if (!isRecord(data) || !Array.isArray(data.items)) {
    await page.parseAsync(data); // throws the envelope's own error
    return;
  }
  const envelope = page.safeParse({ ...data, items: [] });
  const issues = envelope.success ? [] : [...envelope.error.issues];
  issues.push(
    ...(await issuesInSlices(
      (data.items as unknown[]).map((value, i) => ({
        schema: item,
        value,
        path: ["items", i],
      })),
    )),
  );
  fail(issues);
}

/**
 * Validates `data` against `board` (`columns[].cards[]` of `card`, each card's
 * `checklist[]` of `task`) like `board.parseAsync(data)`, a slice of cards and checklist
 * items at a time, so a card with a long checklist is split too.
 */
export async function parseBoardInSlices(
  board: z.ZodType,
  card: z.ZodType,
  task: z.ZodType,
  data: unknown,
): Promise<void> {
  const columns = isRecord(data) ? data.columns : undefined;
  if (
    !Array.isArray(columns) ||
    !columns.every((c) => isRecord(c) && Array.isArray(c.cards))
  ) {
    await board.parseAsync(data);
    return;
  }
  const cols = columns as Record<string, unknown>[];
  const envelope = board.safeParse({
    ...(data as Record<string, unknown>),
    columns: cols.map((c) => ({ ...c, cards: [] })),
  });
  const issues = envelope.success ? [] : [...envelope.error.issues];
  const units = cols.flatMap((c, col) =>
    (c.cards as unknown[]).flatMap((value, index): Unit[] => {
      const path = ["columns", col, "cards", index];
      if (!isRecord(value) || !Array.isArray(value.checklist)) {
        return [{ schema: card, value, path }];
      }
      // The checklist's items one by one, then the rest of the card without them.
      return [
        ...(value.checklist as unknown[]).map((item, i) => ({
          schema: task,
          value: item,
          path: [...path, "checklist", i],
        })),
        { schema: card, value: { ...value, checklist: [] }, path },
      ];
    }),
  );
  issues.push(...(await issuesInSlices(units)));
  fail(issues);
}
