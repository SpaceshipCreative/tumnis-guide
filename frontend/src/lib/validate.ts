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

/** Issues of `schema` over `values`, a slice at a time, each path under `at(index)`. */
async function issuesInSlices(
  schema: z.ZodType,
  values: readonly unknown[],
  at: (index: number) => PropertyKey[],
): Promise<Issue[]> {
  const issues: Issue[] = [];
  for (let i = 0; i < values.length; i++) {
    if (i > 0 && i % SLICE === 0) await yieldToMain();
    const result = schema.safeParse(values[i]);
    if (!result.success) {
      for (const issue of result.error.issues) {
        issues.push({ ...issue, path: [...at(i), ...issue.path] });
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
  issues.push(...(await issuesInSlices(item, data.items, (i) => ["items", i])));
  fail(issues);
}

/**
 * Validates `data` against `board` (`columns[].cards[]` of `card`) like
 * `board.parseAsync(data)`, with the cards of every column a slice at a time.
 */
export async function parseBoardInSlices(
  board: z.ZodType,
  card: z.ZodType,
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
  const cards = cols.flatMap((c, col) =>
    (c.cards as unknown[]).map((value, index) => ({ value, col, index })),
  );
  issues.push(
    ...(await issuesInSlices(
      card,
      cards.map((c) => c.value),
      (i) => {
        const at = cards[i];
        return ["columns", at?.col ?? 0, "cards", at?.index ?? 0];
      },
    )),
  );
  fail(issues);
}
