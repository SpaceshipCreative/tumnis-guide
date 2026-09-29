// The dashboard layout (P0-23, UX 1, UX 11, FR-1.1): one screen at 1280 x 800 with the seed
// set and with ten projects, quick-add in thumb reach on the phone, and the seed projects'
// rule-based health in plain words.
import {
  expect,
  openShell,
  projectCardNamed,
  projectCards,
  scrollsVertically,
  test,
} from "../fixtures";

const PHONE_HEIGHT = 812;
const MIN_TARGET_PX = 44;

test(
  "T-P0-23-10 no scrolling at 1280 x 800",
  { tag: ["@P0-23", "@UX-1"] },
  async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "laptop", "a laptop-only layout rule");
    await openShell(page, "/");
    await expect(projectCards(page)).toHaveCount(3);
    await expect(page.getByRole("region", { name: "Today" })).toBeVisible();
    expect(await scrollsVertically(page), "seed set").toBe(false);

    // The seed plus 7 more projects: the card grid scrolls inside itself, the page not.
    await openShell(page, "/", "ten_projects");
    await expect(projectCards(page)).toHaveCount(10);
    await expect(page.getByRole("region", { name: "Today" })).toBeVisible();
    expect(await scrollsVertically(page), "ten projects").toBe(false);
  },
);

test(
  "T-P0-23-11 quick-add button sits in the bottom third on the phone",
  { tag: ["@P0-23", "@UX-11"] },
  async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "a phone-only control");
    await openShell(page, "/");
    const button = page.getByRole("button", { name: "Quick add" });
    await expect(button).toBeVisible();
    const box = await button.boundingBox();
    expect(box).not.toBeNull();
    expect(box?.y ?? 0).toBeGreaterThanOrEqual((PHONE_HEIGHT * 2) / 3);
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(MIN_TARGET_PX);
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(MIN_TARGET_PX);
  },
);

test(
  "T-P0-23-12 seed projects show their health",
  { tag: ["@P0-23", "@FR-1.1"] },
  async ({ page }) => {
    await openShell(page, "/");
    await expect(projectCardNamed(page, "Acme brand refresh")).toContainText(
      "Blocked: waiting on you",
    );
    await expect(projectCardNamed(page, "Authenticity course")).toContainText(
      "At risk",
    );
    await expect(projectCardNamed(page, "Tumnis dogfood")).toContainText(
      "On track",
    );
  },
);
