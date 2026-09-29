// A0.5 · Preview safety, Playwright part (@smoke). The pytest boot matrix is
// backend/tests/acceptance/test_a0_5_preview_safety.py (T-P0-05-05).
// Phase 0 acceptance, committed red by P0-05. Turns green with P0-04.
import { expect, test } from "../fixtures";

test(
  "A0.5 preview shows the app shell and the sign-in form at 375 px",
  { tag: ["@A0.5", "@REL-7", "@smoke", "@P0-05"] },
  async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(process.env.PREVIEW_URL ?? "/");
    await expect(page.getByRole("main")).toBeVisible({ timeout: 3_000 });
    await expect(page.getByRole("form", { name: "Sign in" })).toBeVisible({
      timeout: 3_000,
    });
  },
);
