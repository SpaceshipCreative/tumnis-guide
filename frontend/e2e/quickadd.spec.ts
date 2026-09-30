// Quick add (P0-25, FR-3.3, FR-3.10, REL-2, journey J2): from any route at both widths a
// capture lands in under 3 s, and a response lost on the way back still makes exactly
// one task (the queue retries with the same Idempotency-Key; the server replays).
import {
  expect,
  listTasks,
  projectIdByName,
  quickAdd,
  quickAddDialog,
  recordedWrites,
  test,
} from "./fixtures";

const DOGFOOD = "Tumnis dogfood";
const PROJECT = { typed: "dogf", name: DOGFOOD };

test(
  "T-P0-25-12 quick-add from any route",
  { tag: ["@J2", "@FR-3.3", "@P0-25"] },
  async ({ signedInPage: page }, testInfo) => {
    test.fail();
    const phone = testInfo.project.name === "phone";
    const dogfood = await projectIdByName(page.request, DOGFOOD);
    const routes = ["/", `/projects/${dogfood}`, "/settings/account"];
    const titles: string[] = [];

    for (const [i, route] of routes.entries()) {
      await page.goto(route);
      await page.getByRole("main").waitFor();
      const title = `Quick add ${testInfo.project.name} ${String(i + 1)}`;
      titles.push(title);
      const started = Date.now();
      await quickAdd(page, phone, title, PROJECT);
      await expect(page.getByText(`Added to ${DOGFOOD}`)).toBeVisible();
      await expect(quickAddDialog(page)).toBeHidden();
      expect(Date.now() - started, route).toBeLessThan(3_000);
      await expect(page.locator("body")).toHaveAttribute(
        "data-offline-queue-state",
        "idle",
      );
    }

    const tasks = await listTasks(page.request, dogfood);
    for (const title of titles) {
      expect(tasks.filter((t) => t.title === title)).toHaveLength(1);
    }
  },
);

test.describe("a lost response", () => {
  // page.route sees every request only without a service worker in between.
  test.use({ serviceWorkers: "block" });

  test(
    "T-P0-25-13 a lost response still creates exactly one task",
    { tag: ["@FR-3.10", "@REL-2", "@P0-25"] },
    async ({ signedInPage: page }, testInfo) => {
      test.fail();
      const phone = testInfo.project.name === "phone";
      const dogfood = await projectIdByName(page.request, DOGFOOD);
      const title = `Lost response ${testInfo.project.name}`;
      let first = true;
      let key = "";
      await page.route("**/v1/tasks", async (route) => {
        if (route.request().method() === "POST" && first) {
          first = false;
          key = (await route.request().headerValue("idempotency-key")) ?? "";
          await route.fetch(); // the server creates the task...
          await route.abort("failed"); // ...and the page never hears back
          return;
        }
        await route.continue();
      });

      await page.goto(`/projects/${dogfood}?view=tasks`);
      await page.getByRole("main").waitFor();
      await quickAdd(page, phone, title, PROJECT);
      await expect(page.locator("body")).toHaveAttribute(
        "data-offline-queue-state",
        "idle",
        { timeout: 15_000 },
      );
      expect(first).toBe(false);
      expect(key).not.toBe("");

      const tasks = await listTasks(page.request, dogfood);
      expect(tasks.filter((t) => t.title === title)).toHaveLength(1);
      const sent = (
        await recordedWrites(page.request, {
          path: "/v1/tasks",
          method: "POST",
        })
      ).filter((w) => w.idempotency_key === key);
      expect(sent.map((w) => w.replayed)).toEqual([false, true]);
    },
  );
});
