// Smoke test (P0-04): the app shell answers. Runs against the compose.test stack,
// and against each PR preview (preview.yml: --grep @smoke --project phone).
import { expect, test } from "./fixtures";

test(
  "T-P0-04-13 app shell loads",
  { tag: ["@smoke", "@P0-04", "@REL-7", "@FR-12.4"] },
  async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("main")).toBeVisible({ timeout: 3_000 });
  },
);
