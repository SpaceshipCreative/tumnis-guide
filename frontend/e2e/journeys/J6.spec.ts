// A1.3 · No gap big enough (journey J6). Phase 1 acceptance, committed red on
// the phase's first day. Green with P1-11 (fit offers, split and move) and the
// scriptable Google Calendar fake plus `calendar-sync` tick (#164, APP-05);
// its `test.fail()` came off once CI run 36966799150 showed it passing.
import { expect, test } from "../fixtures";
import {
  ACME,
  fitOfferRow,
  getDayCalendar,
  getPlan,
  getTask,
  insideFreeBlock,
  minutesOf,
  MONDAY,
  publishMondayPlan,
  squeezeMondayCalendar,
  tasksOfProject,
} from "../phase1";

const PROPOSAL = "Write Acme proposal"; // seed: Human, 90 minutes, due today

test(
  "A1.3 split or move offer",
  { tag: ["@A1.3", "@J6", "@P1-11"] },
  async ({ signedInPage: page, fakes }) => {
    await page.clock.install({ time: new Date("2026-03-09T12:25:00Z") });
    await squeezeMondayCalendar(fakes);
    const proposal = (await tasksOfProject(page.request, ACME)).find(
      (t) => t.title === PROPOSAL,
    );
    expect(proposal?.estimate_minutes).toBe(90);
    const proposalId = proposal?.id ?? "";

    // 1. The master picks the 90-minute task first; the plan is the master's,
    // the other picks sit inside free blocks, the 90-minute task gets no block
    // and a `Doesn't fit today` row with Split (60 + 30) and Move; a plan_issue
    // review item exists.
    const plan = await publishMondayPlan(
      page.request,
      fakes,
      "plan__ninety_first",
    );
    await page.goto("/");
    expect(plan.source).toBe("master");
    const calendar = await getDayCalendar(page.request, MONDAY);
    expect(
      Math.max(...calendar.free_blocks.map((b) => b.minutes)),
    ).toBeLessThan(90);
    expect(plan.items.map((i) => i.task_id)).not.toContain(proposalId);
    for (const item of plan.items) {
      if (item.block !== null) {
        expect(insideFreeBlock(item.block, calendar.free_blocks)).toBe(true);
      }
    }
    const issue = plan.issues.find((i) => i.task_id === proposalId);
    expect(issue?.offer.split).toEqual([60, 30]);
    expect(issue?.offer.move_to).toBe("2026-03-10");
    expect(issue?.review_item_id).toBeTruthy();
    const offer = fitOfferRow(page, PROPOSAL);
    await expect(offer).toContainText("No 90-minute gap today");
    await expect(offer.getByRole("button", { name: /^Split/ })).toContainText(
      "60 + 30",
    );
    await expect(
      offer.getByRole("button", { name: "Move to Tue 10 Mar" }),
    ).toBeVisible();

    // 2 and 3. Open the row, Split, accept the suggested split: two Human
    // subtasks of 60 and 30 minutes, each with the parent's first action; the
    // issue is resolved.
    await offer.click();
    await offer.getByRole("button", { name: /^Split/ }).click();
    await page.getByRole("button", { name: "Accept split" }).click();
    await expect(offer).toBeHidden();
    const parent = await getTask(page.request, proposalId);
    const subtasks = (await tasksOfProject(page.request, ACME)).filter(
      (t) => t.parent_id === proposalId,
    );
    expect(subtasks.map((t) => t.estimate_minutes).sort()).toEqual([30, 60]);
    for (const subtask of subtasks) {
      expect(subtask.label).toBe("human");
      expect(subtask.first_action).toBe(parent.first_action);
    }
    const resolved = (await getPlan(page.request, MONDAY)).issues.find(
      (i) => i.id === issue?.id,
    );
    expect(resolved?.resolved_at).toBeTruthy();

    // 4. Re-plan: the 60-minute subtask is planned inside the 60-minute block.
    await page.getByRole("button", { name: "Re-plan" }).click();
    const sixty = subtasks.find((t) => t.estimate_minutes === 60);
    const sixtyBlock = calendar.free_blocks.find((b) => b.minutes === 60);
    await expect
      .poll(async () => {
        const replanned = await getPlan(page.request, MONDAY);
        return replanned.items.find((i) => i.task_id === sixty?.id)?.block;
      })
      .not.toBeUndefined();
    const replanned = await getPlan(page.request, MONDAY);
    expect(replanned.trigger).toBe("replan");
    const block = replanned.items.find((i) => i.task_id === sixty?.id)?.block;
    expect(block).toBeTruthy();
    expect(minutesOf(block ?? { start: "", end: "" })).toBe(60);
    expect(
      insideFreeBlock(block ?? { start: "", end: "" }, [
        sixtyBlock ?? { start: "", end: "" },
      ]),
    ).toBe(true);
  },
);
