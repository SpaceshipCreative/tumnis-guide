// A2.6 · Nudge and Coach on a fixed clock (journey J8). Phase 2 acceptance, committed
// red on the phase's first day. Turns green with P2-15; the `test.fail()` came off once
// CI run 36973900377 showed it passing (decision 78). Both clocks move together: the
// server's through `POST /v1/test/clock` and the `focus-wake` tick, the page's through
// `page.clock` (https://playwright.dev/docs/clock); the `page` fixture keeps the server
// clock with `page.clock.install`.
import { expect, setServerClock, test } from "../fixtures";
import {
  FOCUS_START,
  advanceBothClocks,
  focusBar,
  focusLevel,
  focusMessages,
  focusMessagesOf,
  localTime,
  plus,
  publishProposalPlan,
  setFocusLevel,
} from "../phase2";

const DAY_ONE = "2026-03-10"; // Tuesday
const DAY_TWO = "2026-03-11";
const ATTRIBUTION = /^(Quiet|Nudge|Coach|Guardrail) · [a-z_]+/;

test(
  "A2.6 Nudge and Coach fire the right focus events on a fixed clock",
  { tag: ["@A2.6", "@J8", "@FR-10.2", "@FR-10.4", "@P2-15"] },
  async ({ signedInPage: page, fakes }) => {
    // The plan for Tuesday with `Write proposal` blocked 10:00 to 10:50 local, then
    // both clocks at 09:50 local (13:50Z).
    await setServerClock(page.request, localTime(DAY_ONE, "08:30"));
    const proposal = await publishProposalPlan(page.request, fakes, DAY_ONE);
    await page.clock.install({ time: FOCUS_START });
    await page.goto("/");
    const bar = focusBar(page);
    const checkIns = focusMessagesOf(page, "check_in_due");

    // Nudge: 10:00 block_start with the first action and its attribution.
    await setFocusLevel(page.request, "nudge");
    let now = await advanceBothClocks(
      page,
      fakes,
      FOCUS_START,
      localTime(DAY_ONE, "10:00"),
    );
    const blockStart = focusMessagesOf(page, "block_start");
    await expect(blockStart).toHaveCount(1);
    await expect(blockStart).toContainText(proposal.first_action ?? "");
    await expect(blockStart).toContainText("Nudge · block_start");
    await expect(checkIns).toHaveCount(0);

    // 10:15 without starting the task: one not_started.
    now = await advanceBothClocks(
      page,
      fakes,
      now,
      localTime(DAY_ONE, "10:15"),
    );
    await expect(focusMessagesOf(page, "not_started")).toHaveCount(1);
    await expect(checkIns).toHaveCount(0);

    // 18:00: one day_end; no check_in_due at any time.
    now = await advanceBothClocks(
      page,
      fakes,
      now,
      localTime(DAY_ONE, "18:00"),
    );
    await expect(focusMessagesOf(page, "day_end")).toHaveCount(1);
    await expect(checkIns).toHaveCount(0);

    // Coach, the next day: the plan again, then start the task at 10:00.
    now = await advanceBothClocks(
      page,
      fakes,
      now,
      localTime(DAY_TWO, "08:30"),
    );
    await publishProposalPlan(page.request, fakes, DAY_TWO);
    await setFocusLevel(page.request, "coach");
    const started = localTime(DAY_TWO, "10:00");
    now = await advanceBothClocks(page, fakes, now, started);
    await bar.getByRole("button", { name: "Start" }).click();

    // +25: the first check-in; still on it.
    now = await advanceBothClocks(page, fakes, now, plus(started, 25));
    await expect(checkIns).toHaveCount(1);
    await bar.getByRole("button", { name: "Still on it" }).click();

    // +50: the second; still on it again (two in a row double the cadence).
    now = await advanceBothClocks(page, fakes, now, plus(started, 50));
    await expect(checkIns).toHaveCount(2);
    await bar.getByRole("button", { name: "Still on it" }).click();

    // +75: nothing; +100 (50 minutes after the second answer): the next one.
    now = await advanceBothClocks(page, fakes, now, plus(started, 75));
    await expect(checkIns).toHaveCount(2);
    now = await advanceBothClocks(page, fakes, now, plus(started, 100));
    await expect(checkIns).toHaveCount(3);
    await expect(checkIns.last()).toContainText("Coach · check_in_due");

    // Every message shows its level and rule.
    const messages = await focusMessages(page).all();
    for (const message of messages) {
      await expect(message.getByTestId("focus-attribution")).toHaveText(
        ATTRIBUTION,
      );
    }

    // Snooze moves the next check-in 15 minutes out.
    await bar.getByRole("button", { name: "Snooze" }).click();
    now = await advanceBothClocks(page, fakes, now, plus(started, 114));
    await expect(checkIns).toHaveCount(3);
    await advanceBothClocks(page, fakes, now, plus(started, 115));
    await expect(checkIns).toHaveCount(4);

    // "Less of this" lowers today's level to Nudge, and the focus bar says so.
    await bar.getByRole("button", { name: "Less of this" }).click();
    await expect(focusLevel(page)).toContainText("Nudge");
  },
);
