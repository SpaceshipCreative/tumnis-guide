// The dashboard layout with Just added items (P0-23 and P1-11, UX 1, FR-1.2). APP-F02
// (application test): on a 1280 x 800 laptop, the left column gave Today only its header
// row once Just added held items, so the plan's Accept, Swap and Remove could not be
// clicked, and the column pushed the page past one screen (T-P0-23-10).
import { expect, scrollsVertically, section, test } from "../fixtures";
import { ACME, createTask, publishMondayPlan, todayItems } from "../phase1";

// Monday 08:25 in America/New_York, five minutes before the plan time (as J1).
const BEFORE_PLAN = new Date("2026-03-09T12:25:00Z");
const JUST_ADDED_TITLES = [
  "Draft the Acme homepage copy for the spring campaign",
  "Collect the logo files from the Acme brand team",
  "Book a review call with Acme about the launch checklist",
];
const PLAN_ACTIONS = ["Accept", "Swap", "Remove"];

test(
  "APP-F02 the Today card keeps its plan actions usable at 1280 x 800 with Just added items",
  { tag: ["@P0-23", "@P1-11", "@UX-1", "@FR-1.2"] },
  async ({ signedInPage: page, seededApp, fakes }, testInfo) => {
    test.skip(testInfo.project.name !== "laptop", "a laptop-only layout rule");
    test.fail();
    // The acceptance set holds the planner's agent and the tasks its plan picks.
    await seededApp.reset("acceptance");
    await page.clock.install({ time: BEFORE_PLAN });
    await publishMondayPlan(page.request, fakes);
    for (const title of JUST_ADDED_TITLES) {
      await createTask(page.request, ACME, title);
    }

    await page.goto("/");
    await expect(section(page, "Just added").getByRole("listitem")).toHaveCount(
      JUST_ADDED_TITLES.length,
    );
    const items = todayItems(page);
    await expect(items).toHaveCount(4);
    expect(await scrollsVertically(page), "with Just added items").toBe(false);

    // Before anything scrolls, the first plan item's controls show whole: the
    // viewport ratio counts what the Today card's own scroller clips.
    const first = items.first();
    for (const name of PLAN_ACTIONS) {
      await expect(first.getByRole("button", { name })).toBeInViewport({
        ratio: 1,
      });
    }
    for (const name of PLAN_ACTIONS) {
      await first.getByRole("button", { name }).click({ trial: true });
    }
    await first.getByRole("button", { name: "Accept" }).click();
    await expect(first).toHaveAttribute("data-state", "accepted");
  },
);
