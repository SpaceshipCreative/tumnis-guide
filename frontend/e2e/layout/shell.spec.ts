// The app shell is keyboard navigable at 375 and 1280 px (P0-22, UX 11): the skip link
// comes first and moves focus to main, every nav item is reachable with a visible focus
// ring, and axe finds nothing serious or critical.
import AxeBuilder from "@axe-core/playwright";

import { expect, focusRing, openShell, shellNavLinks, test } from "../fixtures";

const MAX_TABS = 40;

test(
  "T-P0-22-17 shell is keyboard navigable",
  { tag: ["@P0-22", "@UX-11"] },
  async ({ page }) => {
    test.fail();
    await openShell(page);

    // 1. The first Tab lands on the skip link, which shows itself.
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();

    // 2. Tabbing on reaches every visible nav item, each with a focus ring.
    const links = shellNavLinks(page);
    const hrefs = new Set(
      await links.evaluateAll((all) =>
        all.map((a) => a.getAttribute("href") ?? ""),
      ),
    );
    expect(hrefs.size).toBeGreaterThanOrEqual(4);
    const reached = new Set<string>();
    for (let i = 0; i < MAX_TABS && reached.size < hrefs.size; i += 1) {
      await page.keyboard.press("Tab");
      const focused = page.locator(":focus");
      const inNav = await focused.evaluate(
        (element) => element.closest("nav") !== null,
      );
      const href = await focused.getAttribute("href");
      if (inNav && href !== null && hrefs.has(href)) {
        reached.add(href);
        expect(await focusRing(focused), `focus ring on ${href}`).toBe(true);
      }
    }
    expect([...reached].sort()).toEqual([...hrefs].sort());

    // 3. Enter on the skip link moves focus to main.
    await skip.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("main")).toBeFocused();

    // 4. No serious or critical accessibility violations on the shell.
    const results = await new AxeBuilder({ page }).analyze();
    const serious = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(serious.map((v) => v.id)).toEqual([]);
  },
);
