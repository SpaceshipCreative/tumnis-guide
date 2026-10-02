// A1.2 · Morning plan (journey J1). Phase 1 acceptance, committed red on the
// phase's first day. Green with P1-09 (calendar), P1-10 (free blocks) and
// P1-11 (the plan and the Today panel), plus the Swap fix in #164; its
// `test.fail()` came off once CI run 36966799150 showed it passing.
import { expect, section, setServerClock, test } from "../fixtures";
import {
  estimateChip,
  firstAction,
  getDayCalendar,
  getPlan,
  getTask,
  insideFreeBlock,
  labelChip,
  MASTER,
  MONDAY,
  MONDAY_PLAN_TIME,
  planBlock,
  planProject,
  planReason,
  runnerScript,
  stripFreeBlocks,
  todayItems,
} from "../phase1";

// Monday 08:25 in America/New_York, five minutes before the plan time.
const BEFORE_PLAN = new Date("2026-03-09T12:25:00Z");
const TIME_RANGE = /\d{1,2}:\d{2}\s*[–-]\s*\d{1,2}:\d{2}/;

test(
  "A1.2 morning plan inside free blocks",
  { tag: ["@A1.2", "@J1", "@FR-4.3", "@FR-1.2", "@P1-11"] },
  async ({ signedInPage: page, fakes }) => {
    // Seed day: busy 09:00-10:00, 12:00-13:00 and 15:00-15:30 local across
    // two fake Google accounts; the master answers with 4 picks, one AI-only.
    await page.clock.install({ time: BEFORE_PLAN });
    await fakes.runner.script(
      runnerScript(MASTER, "plan", "plan__monday_four_picks"),
    );

    // 1. Server clock to 08:30, one planner tick.
    await setServerClock(page.request, MONDAY_PLAN_TIME);
    await fakes.tick("planner-tick");

    // 2. Four items (3 to 5), each with project, label, estimate (not on the
    // AI item), first action and reason; Human and Hybrid items show a time
    // range inside a highlighted free block; the AI item runs anytime.
    await page.goto("/");
    const items = todayItems(page);
    await expect(items).toHaveCount(4);
    const plan = await getPlan(page.request, MONDAY);
    const calendar = await getDayCalendar(page.request, MONDAY);
    expect(plan.items).toHaveLength(4);
    await expect(stripFreeBlocks(page)).toHaveCount(
      calendar.free_blocks.length,
    );
    const ordered = [...plan.items].sort((a, b) => a.position - b.position);
    for (const [index, item] of ordered.entries()) {
      const task = await getTask(page.request, item.task_id);
      const row = items.nth(index);
      await expect(row).toContainText(task.title);
      await expect(planProject(row)).not.toHaveText("");
      await expect(labelChip(row)).not.toHaveText("");
      await expect(firstAction(row)).not.toHaveText("");
      await expect(planReason(row)).toHaveText(item.reason);
      expect(item.reason).not.toBe("");
      if (task.label === "ai") {
        await expect(estimateChip(row)).toHaveCount(0);
        await expect(planBlock(row)).toHaveText("Runs anytime");
        expect(item.block).toBeNull();
      } else {
        await expect(estimateChip(row)).toBeVisible();
        await expect(planBlock(row)).toHaveText(TIME_RANGE);
        expect(item.block).not.toBeNull();
        expect(
          insideFreeBlock(
            item.block ?? { start: "", end: "" },
            calendar.free_blocks,
          ),
        ).toBe(true);
      }
    }

    // 3. Accept the first with its button, the second with the `a` key: both
    // show the accepted state and their tasks move to Today.
    const [first, second, third, fourth] = ordered;
    await items.nth(0).getByRole("button", { name: "Accept" }).click();
    await items.nth(1).focus();
    await page.keyboard.press("a");
    for (const index of [0, 1]) {
      await expect(items.nth(index)).toHaveAttribute("data-state", "accepted");
    }
    for (const item of [first, second]) {
      expect((await getTask(page.request, item?.task_id ?? "")).status).toBe(
        "today",
      );
    }

    // 4. Swap the third for the first alternative: it takes position 3 with a
    // block inside a free block.
    await items.nth(2).getByRole("button", { name: "Swap" }).click();
    const picker = page.getByRole("dialog", { name: "Swap" });
    await picker.getByRole("option").first().click();
    await expect(picker).toBeHidden();
    const swapped = (await getPlan(page.request, MONDAY)).items.find(
      (i) => i.position === third?.position && !i.removed_at,
    );
    expect(swapped?.task_id).toBeDefined();
    expect(swapped?.task_id).not.toBe(third?.task_id);
    const swappedTask = await getTask(page.request, swapped?.task_id ?? "");
    await expect(items.nth(2)).toContainText(swappedTask.title);
    if (swappedTask.label !== "ai") {
      expect(
        insideFreeBlock(
          swapped?.block ?? { start: "", end: "" },
          calendar.free_blocks,
        ),
      ).toBe(true);
    }

    // 5. Remove the fourth: it leaves the panel and stays in the backlog.
    const removedTask = await getTask(page.request, fourth?.task_id ?? "");
    await items.nth(3).getByRole("button", { name: "Remove" }).click();
    await expect(items).toHaveCount(3);
    await expect(section(page, "Today")).not.toContainText(removedTask.title);
    expect((await getTask(page.request, removedTask.id)).status).toBe(
      "backlog",
    );

    // 6. After a reload the state holds, and Monday still has one plan.
    await page.reload();
    await expect(items).toHaveCount(3);
    for (const index of [0, 1]) {
      await expect(items.nth(index)).toHaveAttribute("data-state", "accepted");
    }
    await expect(items.nth(2)).toContainText(swappedTask.title);
    const after = await getPlan(page.request, MONDAY);
    expect(after.id).toBe(plan.id);
    expect(after.status).toBe("published");
    expect(after.trigger).toBe("morning");
  },
);
