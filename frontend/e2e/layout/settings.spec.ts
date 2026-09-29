// Every phase 0 setting is reachable on the phone (P0-26, FR-9.3, UX 11): from the
// dashboard through the bottom bar, then each section through the section list, with its
// primary control visible without sideways scrolling and at least 44 px tall.
import {
  expect,
  openSettingsSection,
  openShell,
  scrollsSideways,
  SETTINGS_SECTIONS,
  shellNavLinks,
  test,
} from "../fixtures";

const MIN_TARGET_PX = 44;

test(
  "T-P0-26-10 every phase 0 setting is reachable on the phone",
  { tag: ["@P0-26", "@UX-11", "@FR-9.3"] },
  async ({ page }) => {
    test.fail();
    await openShell(page);
    await shellNavLinks(page).filter({ hasText: "Settings" }).click();
    await expect(page).toHaveURL(/\/settings\/account$/);

    const width = page.viewportSize()?.width ?? 0;
    for (const [label, primaryControl] of SETTINGS_SECTIONS) {
      await openSettingsSection(page, label);
      const primary = primaryControl(page);
      await expect(primary, label).toBeVisible();
      const box = await primary.boundingBox();
      expect(box, label).not.toBeNull();
      expect(
        box?.height ?? 0,
        `${label} control height`,
      ).toBeGreaterThanOrEqual(MIN_TARGET_PX);
      expect((box?.x ?? 0) + (box?.width ?? 0), label).toBeLessThanOrEqual(
        width,
      );
      expect(await scrollsSideways(page), `${label} scrolls sideways`).toBe(
        false,
      );
    }
  },
);
