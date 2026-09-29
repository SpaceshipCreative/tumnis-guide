// End-to-end harness self test (P0-02). Needs no web server: it only reads
// the viewport each Playwright project gives the page.
import { expect, test } from "./fixtures";

const VIEWPORTS: Readonly<Record<string, { width: number; height: number }>> = {
  phone: { width: 375, height: 812 },
  laptop: { width: 1280, height: 800 },
};

test(
  "T-P0-02-16 projects are phone 375 and laptop 1280",
  { tag: ["@P0-02", "@UX-11"] },
  ({ page }, testInfo) => {
    expect(Object.keys(VIEWPORTS)).toContain(testInfo.project.name);
    expect(page.viewportSize()).toEqual(VIEWPORTS[testInfo.project.name]);
  },
);
