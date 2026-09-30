// Performance timings on the load set (P0-29, PERF-2, NFR Performance, NFR Responsive):
// the dashboard's first paint with data under 1 s on LAN, and quick-add and the review
// queue usable within 3 s of a cold PWA open at phone width. Hardware-sensitive: they run
// in the Performance job only (the e2e job leaves out @P0-29 and @A0.6). Each timing is the median
// of three runs (plan risk: a shared host).
import {
  coldPage,
  expect,
  markStartTime,
  median,
  quickAddUsable,
  serviceWorkerActive,
  test,
} from "../fixtures";

const RUNS = 3;

test(
  "T-P0-29-01 dashboard first paint under 1 s on LAN",
  { tag: ["@P0-29", "@NFR-Performance", "@PERF-2"] },
  async ({ seededApp, signedInPage: page }, testInfo) => {
    test.skip(testInfo.project.name !== "laptop", "laptop width only");
    await seededApp.reset("load"); // 10 projects, 2,000 tasks
    await page.goto("/"); // warm: the service worker installs
    const times: number[] = [];
    for (let run = 0; run < RUNS; run++) {
      await page.reload();
      times.push(await markStartTime(page, "tumnis:dashboard-ready"));
    }
    expect(median(times), `runs: ${times.join(", ")} ms`).toBeLessThan(1_000);
  },
);

test(
  "T-P0-29-02 quick-add usable within 3 s of a cold PWA open at phone width",
  { tag: ["@P0-29", "@NFR-Responsive"] },
  async ({ seededApp, signedInPage: page, context }, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone width only");
    await seededApp.reset("load");
    await page.goto("/");
    expect(await serviceWorkerActive(page)).toBe(true);
    await page.close();

    const times: number[] = [];
    for (let run = 0; run < RUNS; run++) {
      const cold = await coldPage(context);
      const started = Date.now();
      await cold.goto("/", { waitUntil: "commit" });
      expect(await quickAddUsable(cold)).toBe(true);
      times.push(Date.now() - started);
      console.log(
        `TIMING ${testInfo.project.name} ${testInfo.title}: usable at ${String(times.at(-1))} ms`,
      );
      await cold.close();
    }
    expect(median(times), `runs: ${times.join(", ")} ms`).toBeLessThan(3_000);
  },
);

test(
  "T-P0-29-03 review queue usable within 3 s of a cold PWA open at phone width",
  { tag: ["@P0-29", "@NFR-Responsive"] },
  async ({ seededApp, signedInPage: page, context }, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone width only");
    await seededApp.reset("load");
    await page.goto("/");
    expect(await serviceWorkerActive(page)).toBe(true);
    await page.close();

    const times: number[] = [];
    for (let run = 0; run < RUNS; run++) {
      const cold = await coldPage(context);
      await cold.goto("/review", { waitUntil: "commit" });
      times.push(await markStartTime(cold, "tumnis:review-ready"));
      await cold.close();
    }
    expect(median(times), `runs: ${times.join(", ")} ms`).toBeLessThan(3_000);
  },
);
