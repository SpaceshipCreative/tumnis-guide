// A0.6 · Performance budgets, timing part. T-P0-05-06.
// Phase 0 acceptance, committed red by P0-05. The k6 threshold
// (perf/k6/quickadd.js, perf/baseline.json) arrives with P0-29 and the bundle
// budget (scripts/ci/check_bundle.mjs) with P0-22. Hardware-sensitive: it runs
// in the Performance job on the homelab runner (P0-29). Turns green with P0-22,
// P0-23, P0-25 and P0-29.
import { expect, markStartTime, test } from "../fixtures";

test(
  "A0.6 first paint and cold open budgets",
  {
    tag: ["@A0.6", "@PERF-2", "@NFR-Performance", "@NFR-Responsive", "@P0-05"],
  },
  async ({ seededApp, signedInPage: page, context }, testInfo) => {
    test.fail();
    await seededApp.reset("load"); // 10 projects, 2,000 tasks

    if (testInfo.project.name === "laptop") {
      // 1. Dashboard ready (cards and Today rendered with data) under 1 s.
      await page.goto("/");
      await page.reload();
      expect(await markStartTime(page, "tumnis:dashboard-ready")).toBeLessThan(
        1_000,
      );
      return;
    }

    // 2. Phone, cold PWA open: service worker installed, then a fresh page.
    await page.goto("/");
    const installed = await page.evaluate(() =>
      navigator.serviceWorker.ready.then((r) => r.active !== null),
    );
    expect(installed).toBe(true);

    const quickAddPage = await context.newPage();
    await quickAddPage.goto("/");
    await quickAddPage.getByRole("button", { name: "Quick add" }).click();
    expect(
      await markStartTime(quickAddPage, "tumnis:quickadd-ready"),
    ).toBeLessThan(3_000);
    const title = quickAddPage
      .getByRole("dialog", { name: "Quick add" })
      .getByRole("textbox", { name: "Title" });
    await expect(title).toBeFocused();
    await quickAddPage.keyboard.type("x");
    await expect(title).toHaveValue("x");

    const reviewPage = await context.newPage();
    await reviewPage.goto("/review");
    expect(await markStartTime(reviewPage, "tumnis:review-ready")).toBeLessThan(
      3_000,
    );
  },
);
