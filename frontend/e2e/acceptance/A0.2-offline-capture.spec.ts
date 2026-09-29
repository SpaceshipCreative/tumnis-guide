// A0.2 · Offline capture. T-P0-05-02.
// Phase 0 acceptance, committed red by P0-05. Turns green with P0-10 (stored
// responses), P0-22 (service worker) and P0-25 (queue and replay).
import {
  expect,
  listTasks,
  projectIdByName,
  quickAdd,
  recordedWrites,
  test,
} from "../fixtures";

const DOGFOOD = "Tumnis dogfood";
const TITLES = ["Offline one", "Offline two"] as const;

test(
  "A0.2 offline capture syncs exactly once",
  { tag: ["@A0.2", "@FR-3.10", "@REL-2", "@P0-05"] },
  async ({ seededApp, signedInPage: page, context }, testInfo) => {
    test.fail();
    const phone = testInfo.project.name === "phone";
    expect(seededApp.baseURL).toBeTruthy();
    const swReady = await page.evaluate(() =>
      navigator.serviceWorker.ready.then((r) => r.active !== null),
    );
    expect(swReady).toBe(true);
    const idle = () =>
      expect(page.locator("body")).toHaveAttribute(
        "data-offline-queue-state",
        "idle",
      );
    const pendingMarks = page.getByTestId("pending-mark");

    // 1. Task count through the API, with the session.
    const dogfood = await projectIdByName(page.request, DOGFOOD);
    const n0 = (await listTasks(page.request, dogfood)).length;
    await page.goto(`/projects/${dogfood}?view=tasks`);

    // 2. Offline.
    await context.setOffline(true);

    // 3. Quick-add two tasks to the dogfood project.
    for (const title of TITLES) {
      await quickAdd(page, phone, title, { typed: "dogf", name: DOGFOOD });
    }

    // 4. Both show in the Tasks view with a pending mark.
    for (const title of TITLES) {
      await expect(page.getByText(title, { exact: true })).toBeVisible();
    }
    await expect(pendingMarks).toHaveCount(2);

    // 5. Reload while offline: the shell comes from the service worker and the
    //    pending items from IndexedDB.
    await page.reload();
    await expect(page.getByRole("main")).toBeVisible();
    for (const title of TITLES) {
      await expect(page.getByText(title, { exact: true })).toBeVisible();
    }
    await expect(pendingMarks).toHaveCount(2);

    // 6. Online: the queue drains and the pending marks clear.
    await context.setOffline(false);
    await idle();
    await expect(pendingMarks).toHaveCount(0);

    // 7. Reload once more (replay on app open); nothing is sent twice.
    await page.reload();
    await idle();
    await expect(pendingMarks).toHaveCount(0);

    const tasks = await listTasks(page.request, dogfood);
    expect(tasks).toHaveLength(n0 + 2);
    for (const title of TITLES) {
      expect(tasks.filter((t) => t.title === title)).toHaveLength(1);
    }

    // Each idempotency key reached the server at most twice; a second request
    // with the same key got the stored response.
    const log = await recordedWrites(page.request, {
      path: "/v1/tasks",
      method: "POST",
    });
    const byKey = new Map<string, boolean[]>();
    for (const write of log) {
      expect(write.idempotency_key).toBeTruthy();
      const key = write.idempotency_key ?? "";
      byKey.set(key, [...(byKey.get(key) ?? []), write.replayed]);
    }
    expect(byKey.size).toBeGreaterThanOrEqual(2);
    for (const replays of byKey.values()) {
      expect(replays.length).toBeLessThanOrEqual(2);
      expect(replays[0]).toBe(false);
      if (replays.length === 2) expect(replays[1]).toBe(true);
    }
  },
);
