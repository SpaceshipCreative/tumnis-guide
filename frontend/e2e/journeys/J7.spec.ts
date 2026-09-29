// A1.6 · Close the day (journey J7). Phase 1 acceptance, committed red on the
// phase's first day. Turns green with P1-18.
import { expect, test } from "../fixtures";
import { arrangeCloseTheDay, closeDayPanel } from "../phase1";

// Monday 2026-03-09 17:40 in America/New_York.
const EVENING = new Date("2026-03-09T21:40:00Z");
const WRITES = new Set(["POST", "PUT", "PATCH", "DELETE"]);

test(
  "A1.6 close the day",
  { tag: ["@A1.6", "@J7", "@P1-18"] },
  async ({ signedInPage: page, fakes }, testInfo) => {
    test.fail();
    const phone = testInfo.project.name === "phone";
    // A published plan with 4 accepted items: 2 Done (one Human by the user,
    // one AI with an agent result link), 2 still Today; one enrichment run
    // finished today. Then both clocks to 17:40 local.
    const { shipped, rolling } = await arrangeCloseTheDay(page.request, fakes);
    await page.clock.install({ time: EVENING });

    // 1. Open the dashboard; Close the day.
    await page.goto("/");
    await page.getByRole("button", { name: "Close the day" }).click();
    const panel = closeDayPanel(page);
    await expect(panel).toBeVisible();

    // 2. Shipped, Agents finished, Queued overnight, Rolls over.
    const part = (name: string) =>
      panel.getByRole("region", { name, exact: true });
    const shippedPart = part("Shipped");
    await expect(shippedPart.getByRole("listitem")).toHaveCount(2);
    for (const task of shipped) {
      await expect(shippedPart).toContainText(task.title);
    }
    const agents = part("Agents finished");
    const aiTask = shipped.find((t) => t.label === "ai");
    await expect(agents).toContainText(aiTask?.title ?? "<no AI task>");
    await expect(agents).toContainText("1 task prepared by agents");
    await expect(part("Queued overnight")).toContainText("Nothing queued");
    const rolls = part("Rolls over");
    await expect(rolls.getByRole("listitem")).toHaveCount(2);
    for (const task of rolling) {
      const row = rolls.getByRole("listitem").filter({ hasText: task.title });
      await expect(row).toContainText("+1 tonight");
      await expect(row.getByTestId("rollover-count")).toHaveText(/^\d+$/);
    }

    // At 375 px the panel is a full-height sheet with the same sections.
    if (phone) {
      const box = await panel.boundingBox();
      const viewport = page.viewportSize();
      expect(box?.height).toBe(viewport?.height);
    }

    // Nothing is required: Done closes the panel without further input, and
    // dismissing the panel sends no write.
    await panel.getByRole("button", { name: "Done" }).click();
    await expect(panel).toBeHidden();
    await page.getByRole("button", { name: "Close the day" }).click();
    await expect(panel).toBeVisible();
    const writes: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (WRITES.has(request.method()) && path.startsWith("/v1/")) {
        writes.push(`${request.method()} ${path}`);
      }
    });
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    await page.goto("/");
    expect(writes).toEqual([]);
  },
);
