// The Mosaic-style shell at 375 and 1280 px (DS-01, ADR-0012, UX 7, UX 11): the theme
// toggle shows and remembers light, dark or the system's choice; the laptop sidebar
// collapses to icons and stays that way; the phone drawer keeps focus inside until Escape;
// the header menus work from the keyboard; and axe finds nothing serious in dark mode.
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, openShell, test } from "../fixtures";

const DARK_BG = "rgb(17, 24, 39)";
const LIGHT_BG = "rgb(243, 244, 246)";

function bodyBackground(page: Page): Promise<string> {
  return page.evaluate(() => getComputedStyle(document.body).backgroundColor);
}

async function chooseTheme(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "Account" }).click();
  await page
    .getByRole("menu", { name: "Account" })
    .getByRole("menuitemradio", { name })
    .click();
}

async function seriousViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => v.id);
}

test(
  "T-DS-01-08 the theme toggle shows and remembers the choice",
  { tag: ["@DS-01", "@UX-11"] },
  async ({ page }) => {
    test.fail();
    await page.emulateMedia({ colorScheme: "light" });
    await openShell(page);
    const html = page.locator("html");
    await expect(html).not.toHaveAttribute("data-theme");
    expect(await bodyBackground(page)).toBe(LIGHT_BG);

    await chooseTheme(page, "Dark");
    await expect(html).toHaveAttribute("data-theme", "dark");
    expect(await bodyBackground(page)).toBe(DARK_BG);

    // A reload keeps the choice, before the app has even mounted.
    await page.reload();
    await expect(html).toHaveAttribute("data-theme", "dark");
    expect(await bodyBackground(page)).toBe(DARK_BG);

    // "System" follows the device again.
    await chooseTheme(page, "System");
    await expect(html).not.toHaveAttribute("data-theme");
    expect(await bodyBackground(page)).toBe(LIGHT_BG);
    await page.emulateMedia({ colorScheme: "dark" });
    expect(await bodyBackground(page)).toBe(DARK_BG);
  },
);

test(
  "T-DS-01-09 the laptop sidebar collapses to icons and stays collapsed",
  { tag: ["@DS-01", "@UX-11"] },
  async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "laptop", "a laptop-only control");
    test.fail();
    await openShell(page);
    const nav = page.getByRole("navigation", { name: "Primary" });
    expect((await nav.boundingBox())?.width ?? 0).toBeGreaterThan(200);

    await nav.getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(
      nav.getByRole("button", { name: "Expand sidebar" }),
    ).toBeVisible();
    await expect
      .poll(async () => (await nav.boundingBox())?.width ?? 0)
      .toBeLessThanOrEqual(96);

    await page.reload();
    await nav.waitFor();
    expect((await nav.boundingBox())?.width ?? 0).toBeLessThanOrEqual(96);
    // Icons only, but each link keeps its name and opens its page.
    await nav.getByRole("link", { name: "Projects", exact: true }).click();
    await expect(page).toHaveURL(/\/projects$/);
  },
);

test(
  "T-DS-01-10 the phone drawer traps focus and closes on Escape",
  { tag: ["@DS-01", "@UX-11"] },
  async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "a phone-only control");
    test.fail();
    await openShell(page);
    const open = page.getByRole("button", { name: "Open menu" });
    await open.click();
    const drawer = page.getByRole("dialog", { name: "Menu" });
    await expect(drawer).toBeVisible();
    await expect
      .poll(async () => (await drawer.boundingBox())?.x ?? -1)
      .toBe(0);
    expect(await seriousViolations(page)).toEqual([]);

    for (let i = 0; i < 12; i += 1) {
      await page.keyboard.press(i % 3 === 2 ? "Shift+Tab" : "Tab");
      const inside = await drawer.evaluate((el) =>
        el.contains(document.activeElement),
      );
      expect(inside, `focus stays in the drawer after ${String(i + 1)}`).toBe(
        true,
      );
    }

    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    await expect(open).toBeFocused();
  },
);

test(
  "T-DS-01-11 the header menus work from the keyboard",
  { tag: ["@DS-01", "@UX-7"] },
  async ({ page }) => {
    test.fail();
    await openShell(page);
    const account = page.getByRole("button", { name: "Account" });
    await account.focus();
    await page.keyboard.press("Enter");
    const menu = page.getByRole("menu", { name: "Account" });
    await expect(menu).toBeVisible();
    await expect(
      menu.getByRole("menuitem", { name: "Settings" }),
    ).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(
      menu.getByRole("menuitemradio", { name: "Light" }),
    ).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(account).toBeFocused();

    const help = page.getByRole("button", { name: "Help" });
    await help.focus();
    await page.keyboard.press("Enter");
    const panel = page.getByRole("region", { name: "Keyboard shortcuts" });
    await expect(panel).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(help).toBeFocused();
  },
);

test(
  "T-DS-01-12 no serious accessibility violations in dark mode",
  { tag: ["@DS-01", "@UX-11"] },
  async ({ page }) => {
    test.fail();
    await page.emulateMedia({ colorScheme: "dark" });
    await openShell(page);
    await expect(page.locator("html")).not.toHaveAttribute("data-theme");
    expect(await bodyBackground(page)).toBe(DARK_BG);
    expect(await seriousViolations(page)).toEqual([]);
  },
);
