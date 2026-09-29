// The board (P0-24, FR-3.2, UX 7): a drag is one move request whose rank sorts between the
// card's new neighbours, and the order survives a reload; the keyboard moves a card to
// the next column (Space, ArrowRight, Space) and the live region says where it landed.
import {
  boardCards,
  expect,
  openProject,
  recordWrites,
  section,
  test,
} from "./fixtures";

const PROJECT = "Acme brand refresh";
const MOVE = /^\/v1\/tasks\/[0-9a-f-]{36}\/move$/;

test(
  "T-P0-24-04 a drag sends one move request with the new board_rank",
  { tag: ["@P0-24", "@FR-3.2"] },
  async ({ page }, testInfo) => {
    test.fail();
    test.skip(
      testInfo.project.name !== "laptop",
      "pointer drag; the phone moves cards by touch (preview check) or the Move menu",
    );
    const projectId = await openProject(page, PROJECT, "?view=board");
    const backlog = section(page, "Backlog");
    const today = section(page, "Today");
    const card = boardCards(backlog).first();
    const title = (await card.getAttribute("data-task-title")) ?? "";
    expect(title).not.toBe("");
    const before = await boardCards(today).evaluateAll((cards) =>
      cards.map((c) => c.getAttribute("data-board-rank") ?? ""),
    );
    expect(before.length).toBeGreaterThanOrEqual(1);
    const writes = recordWrites(page, MOVE);

    // Position 2 of Today: below its first card.
    const box = await today.boundingBox();
    const moved = page.waitForResponse((r) =>
      MOVE.test(new URL(r.url()).pathname),
    );
    await card.dragTo(today, {
      targetPosition: {
        x: (box?.width ?? 200) / 2,
        y: (box?.height ?? 400) - 12,
      },
    });
    expect((await moved).ok()).toBe(true);

    expect(writes).toHaveLength(1);
    const body = writes[0]?.postDataJSON() as {
      column_id: string;
      board_rank: string;
      version: number;
    };
    expect(Object.keys(body).sort()).toEqual([
      "board_rank",
      "column_id",
      "version",
    ]);
    const columns = await page.request.get(`/v1/projects/${projectId}/columns`);
    const todayColumn = (
      (await columns.json()) as { items: { id: string; name: string }[] }
    ).items.find((c) => c.name === "Today");
    expect(body.column_id).toBe(todayColumn?.id);
    expect(typeof body.version).toBe("number");
    const neighbours = [...before].sort();
    expect(body.board_rank > (neighbours.at(0) ?? "")).toBe(true);
    if (neighbours.length > 1)
      expect(body.board_rank < (neighbours[1] ?? "")).toBe(true);

    const titles = () =>
      boardCards(today).evaluateAll((cards) =>
        cards.map((c) => c.getAttribute("data-task-title")),
      );
    await expect.poll(titles).toEqual(expect.arrayContaining([title]));
    const order = await titles();
    expect(order.indexOf(title)).toBe(1);

    await page.reload();
    await expect(boardCards(section(page, "Today")).nth(1)).toHaveAttribute(
      "data-task-title",
      title,
    );
  },
);

test(
  "T-P0-24-05 a keyboard drag moves a card between columns",
  { tag: ["@P0-24", "@UX-7"] },
  async ({ page }) => {
    test.fail();
    await openProject(page, PROJECT, "?view=board");
    const backlog = section(page, "Backlog");
    const card = boardCards(backlog).first();
    const title = (await card.getAttribute("data-task-title")) ?? "";
    const writes = recordWrites(page, MOVE);

    await card.focus();
    await page.keyboard.press("Space");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Space");

    await expect(
      boardCards(section(page, "Today")).filter({
        has: page.getByText(title, { exact: true }),
      }),
    ).toHaveCount(1);
    await expect.poll(() => writes.length).toBe(1);
    await expect(page.locator('[id^="DndLiveRegion"]')).toContainText(
      `${title} was dropped in Today`,
    );
  },
);
