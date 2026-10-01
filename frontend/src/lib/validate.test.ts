// Response validation in slices (PERF-2): the same verdict as the generated validator
// (nothing for a valid response; the same issues at the same paths for an invalid one),
// with the main thread given back between slices of a long list.
import { afterEach, expect, test, vi } from "vitest";
import * as z from "zod";

import { zBoardOut, zCardOut, zTaskOut, zTaskPage } from "../api/zod.gen";
import { makeBoard, makeTask } from "../test/factories";
import { parseBoardInSlices, parsePageInSlices, SLICE } from "./validate";

afterEach(() => {
  vi.unstubAllGlobals();
});

function page(n: number) {
  const items = Array.from({ length: n }, () => makeTask());
  return { items, next_cursor: null, total: n };
}

async function issuesOf(run: () => Promise<unknown>): Promise<unknown> {
  try {
    await run();
  } catch (error) {
    expect(error).toBeInstanceOf(z.ZodError);
    // The same issues; the order may differ (the list's come after the envelope's).
    return (error as z.ZodError).issues
      .map(({ code, path }) => JSON.stringify({ code, path }))
      .sort();
  }
  return "no error";
}

test("[P0-29][PERF-2] a valid task page passes like the generated validator", async () => {
  const data = page(3 * SLICE + 5);
  await expect(zTaskPage.parseAsync(data)).resolves.toBeDefined();
  await expect(
    parsePageInSlices(zTaskPage, zTaskOut, data),
  ).resolves.toBeUndefined();
});

test("[P0-29][PERF-2] an invalid task page fails with the generated validator's issues", async () => {
  const data = page(3 * SLICE + 5) as Record<string, unknown> & {
    items: Record<string, unknown>[];
  };
  data.total = "many";
  data.items[2 * SLICE + 1] = { ...data.items[2 * SLICE + 1], id: "nope" };
  data.items[3] = { ...data.items[3], title: undefined };

  const expected = await issuesOf(() => zTaskPage.parseAsync(data));
  expect(expected).not.toBe("no error");
  expect(
    await issuesOf(() => parsePageInSlices(zTaskPage, zTaskOut, data)),
  ).toEqual(expected);
  // Not an object, or no list: the envelope's own verdict.
  for (const bad of [null, "page", { next_cursor: null, total: 0 }]) {
    expect(
      await issuesOf(() => parsePageInSlices(zTaskPage, zTaskOut, bad)),
    ).toEqual(await issuesOf(() => zTaskPage.parseAsync(bad)));
  }
});

test("[P0-29][PERF-2] the board is checked card by card with the generated verdict", async () => {
  const board = makeBoard({
    columns: [
      {
        name: "Backlog",
        status: "backlog",
        cards: Array.from({ length: 2 * SLICE }, () => ({})),
      },
      { name: "Today", status: "today", cards: [{}, {}, {}] },
    ],
  });
  await expect(
    parseBoardInSlices(zBoardOut, zCardOut, board),
  ).resolves.toBeUndefined();

  const [backlog, today] = board.columns;
  const bad = {
    ...board,
    threshold_min: 1.5,
    columns: [
      {
        ...backlog,
        cards: backlog?.cards.map((card, i) =>
          i === SLICE + 3
            ? { ...card, task: { ...card.task, version: "one" } }
            : card,
        ),
      },
      {
        ...today,
        name: 7,
        cards: today?.cards.map((card, i) =>
          i === 2 ? { ...card, task: { ...card.task, status: "lost" } } : card,
        ),
      },
    ],
  };
  const expected = await issuesOf(() => zBoardOut.parseAsync(bad));
  expect(expected).not.toBe("no error");
  expect(
    await issuesOf(() => parseBoardInSlices(zBoardOut, zCardOut, bad)),
  ).toEqual(expected);
  for (const odd of [null, { columns: "x" }, { columns: [{ cards: 1 }] }]) {
    expect(
      await issuesOf(() => parseBoardInSlices(zBoardOut, zCardOut, odd)),
    ).toEqual(await issuesOf(() => zBoardOut.parseAsync(odd)));
  }
});

test("[P0-29][PERF-2] a long list gives the main thread back between slices", async () => {
  const yields = vi.fn(() => Promise.resolve());
  vi.stubGlobal("scheduler", { yield: yields });

  await parsePageInSlices(zTaskPage, zTaskOut, page(SLICE));
  expect(yields).not.toHaveBeenCalled(); // one slice: no pause

  await parsePageInSlices(zTaskPage, zTaskOut, page(5 * SLICE + 1));
  expect(yields).toHaveBeenCalledTimes(5);
});
