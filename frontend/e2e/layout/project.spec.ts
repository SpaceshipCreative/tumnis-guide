// The project page at both widths (P0-24, FR-2.2, FR-2.8): on a laptop three zones (the
// navigation rail, the centre column, the Context rail); on the phone one column with a
// segmented control and the Context sheet; neither scrolls sideways.
import {
  expect,
  openProject,
  scrollsSideways,
  section,
  test,
} from "../fixtures";

test(
  "T-P0-24-17 project page at both widths",
  { tag: ["@P0-24", "@FR-2.2", "@FR-2.8"] },
  async ({ page }, testInfo) => {
    await openProject(page, "Acme brand refresh");
    const main = page.getByRole("main");
    await expect(main.getByRole("textbox", { name: "New task" })).toBeVisible();
    await expect(section(page, "Up next")).toBeVisible();

    if (testInfo.project.name === "laptop") {
      const nav = page.getByRole("navigation", { name: "Primary" });
      const rail = page.getByRole("complementary", { name: "Context" });
      await expect(nav).toBeVisible();
      await expect(rail).toBeVisible();
      await expect(page.getByRole("tablist", { name: "Views" })).toBeVisible();
      const navBox = await nav.boundingBox();
      const centre = await section(page, "Up next").boundingBox();
      const railBox = await rail.boundingBox();
      // Left to right: navigation, centre column, rail, side by side.
      expect((navBox?.x ?? 0) + (navBox?.width ?? 0)).toBeLessThanOrEqual(
        centre?.x ?? 0,
      );
      expect((centre?.x ?? 0) + (centre?.width ?? 0)).toBeLessThanOrEqual(
        railBox?.x ?? 0,
      );
      await expect(page.getByRole("button", { name: "Context" })).toHaveCount(
        0,
      );
    } else {
      await expect(
        page.getByRole("radiogroup", { name: "Views" }),
      ).toBeVisible();
      await expect(
        page.getByRole("complementary", { name: "Context" }),
      ).toHaveCount(0);
      await page.getByRole("button", { name: "Context" }).click();
      const sheet = page.getByRole("dialog", { name: "Context" });
      await expect(sheet).toBeVisible();
      await expect(sheet.getByRole("button", { name: /^Brief/ })).toBeVisible();
      const sheetBox = await sheet.boundingBox();
      expect(sheetBox?.y ?? 0).toBeGreaterThan(0);
      expect((sheetBox?.y ?? 0) + (sheetBox?.height ?? 0)).toBeLessThanOrEqual(
        812 + 1,
      );
      await page.getByRole("button", { name: "Close" }).click();
      await page.getByRole("radio", { name: "Board" }).click();
      await expect(section(page, "Backlog")).toBeVisible();
    }
    expect(await scrollsSideways(page)).toBe(false);
  },
);
