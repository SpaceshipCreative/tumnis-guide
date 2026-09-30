// A0.1 · Capture to dashboard by hand (journey J2). T-P0-05-01.
// Phase 0 acceptance, committed red by P0-05. Turns green with P0-13 (steps 1
// to 4), P0-17 (5), P0-20 and P0-25 (6 to 8), P0-24 (9 to 11), P0-23 (12); the
// `test.fail()` came off with P0-25, the last of P0-25 and P0-23 to merge.
//
// A1.1 · Quick-add with AI label and enrichment (journey J2). Phase 1
// acceptance, committed red on the phase's first day. Turns green with P1-07
// (label step), P1-03 (placeholder) and P1-08 (estimate and first action); the
// `test.fail()` comes off when P1-08, the last of them, merges.
import type { Request } from "@playwright/test";

import {
  boardColumns,
  expect,
  listTasks,
  projectCard,
  projectIdByName,
  quickAdd,
  quickAddDialog,
  openQuickAdd,
  section,
  seedUser,
  taskCards,
  test,
  totp,
} from "../fixtures";
import {
  ACME,
  ACME_AGENT,
  estimateChip,
  firstAction,
  labelChip,
  quickAddedRow,
  runnerScript,
  taskDrawer,
} from "../phase1";

const NOW = new Date("2026-03-09T14:00:00Z"); // 10:00 in America/New_York
const TITLE = "Send logo drafts to Acme";
const PROJECT = "Acme rebrand";
const WRITES = new Set(["POST", "PUT", "PATCH", "DELETE"]);

test(
  "A0.1 sign in, create project, quick-add, board, today, dashboard",
  {
    tag: [
      "@A0.1",
      "@J2",
      "@FR-1.1",
      "@FR-1.2",
      "@FR-2.2",
      "@FR-3.3",
      "@SEC-1",
      "@P0-05",
    ],
  },
  async ({ seededApp, page, context }, testInfo) => {
    const phone = testInfo.project.name === "phone";
    const user = seedUser();
    expect(seededApp.baseURL).toBeTruthy(); // seed set loaded
    expect(await context.cookies()).toEqual([]);
    await page.clock.install({ time: NOW });

    // 1. No session: / redirects to /login.
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);

    // 2. Password first; the TOTP step follows and no session cookie exists yet.
    await page.getByLabel("Email").fill(user.email);
    await page.getByLabel("Password").fill(user.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    const code = page.getByLabel("Authentication code");
    await expect(code).toBeVisible();
    const early = await context.cookies();
    expect(
      early.find((c) => c.name === "__Host-tumnis_session"),
    ).toBeUndefined();

    // 3. TOTP from the seed secret at the fixed clock time lands on the dashboard.
    await code.fill(totp(user.totpSecret, NOW));
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page).toHaveURL(/\/$/);

    // 4. Session and CSRF cookies (R-18).
    const cookies = await context.cookies();
    const session = cookies.find((c) => c.name === "__Host-tumnis_session");
    expect(session).toMatchObject({
      httpOnly: true,
      secure: true,
      sameSite: "Lax",
      path: "/",
    });
    const csrf = cookies.find((c) => c.name === "__Host-tumnis_csrf");
    expect(csrf).toBeDefined();
    expect(csrf?.httpOnly).toBe(false);

    // 5. New project from the project list.
    await page.getByRole("link", { name: "Projects" }).click();
    await page.getByRole("button", { name: "New project" }).click();
    await page.getByLabel("Name").fill(PROJECT);
    await page.getByLabel("Client").fill("Acme");
    await page.getByLabel("Goal").fill("Ship the new logo");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(PROJECT);
    const projectId = new URL(page.url()).pathname.split("/").at(-1) ?? "";

    // 6. Back to the dashboard; `/` on a keyboard, the Quick add button on a phone.
    await page.goto("/");
    await openQuickAdd(page, phone);
    const dialog = quickAddDialog(page);
    await expect(dialog).toBeVisible();

    // 7. Title, project typeahead, Enter.
    await dialog.getByRole("textbox", { name: "Title" }).fill(TITLE);
    await dialog.getByRole("combobox", { name: "Project" }).fill("acm");
    await page.getByRole("option", { name: PROJECT, exact: true }).click();
    await dialog.getByRole("textbox", { name: "Title" }).press("Enter");

    // 8. Toast, dialog closed.
    await expect(page.getByText(`Added to ${PROJECT}`)).toBeVisible();
    await expect(dialog).toBeHidden();

    // 9. Tasks view: the task is under "Up next", exactly once.
    await page.goto(`/projects/${projectId}?view=tasks`);
    await expect(taskCards(page, TITLE)).toHaveCount(1);
    await expect(taskCards(page, TITLE, section(page, "Up next"))).toHaveCount(
      1,
    );

    // 10. Board view: the card is in Backlog, exactly once.
    await page.goto(`/projects/${projectId}?view=board`);
    await expect(taskCards(page, TITLE)).toHaveCount(1);
    const card = taskCards(page, TITLE, section(page, "Backlog"));
    await expect(card).toHaveCount(1);

    // 11. Move the card to Today: one move request, nothing else writes the task.
    const [task] = (await listTasks(page.request, projectId)).filter(
      (t) => t.title === TITLE,
    );
    expect(task).toBeDefined();
    const taskId = task?.id ?? "";
    const today = (await boardColumns(page.request, projectId)).find(
      (c) => c.name === "Today",
    );
    const writes: Request[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (
        WRITES.has(request.method()) &&
        path.startsWith(`/v1/tasks/${taskId}`)
      ) {
        writes.push(request);
      }
    });
    const moved = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname === `/v1/tasks/${taskId}/move`,
    );
    if (phone) {
      // Touch drag is covered by P0-24's component tests; here the keyboard path.
      await card.getByRole("button", { name: "Move" }).press("Enter");
      await page.getByRole("menuitem", { name: "Today" }).press("Enter");
    } else {
      await card.dragTo(section(page, "Today"));
    }
    expect((await moved).ok()).toBe(true);
    await expect(taskCards(page, TITLE, section(page, "Today"))).toHaveCount(1);

    expect(writes).toHaveLength(1);
    const [move] = writes;
    expect(move?.method()).toBe("POST");
    expect(new URL(move?.url() ?? "").pathname).toBe(
      `/v1/tasks/${taskId}/move`,
    );
    const body = move?.postDataJSON() as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual([
      "board_rank",
      "column_id",
      "version",
    ]);
    expect(body.column_id).toBe(today?.id);
    expect(body.version).toBe(task?.version);
    expect(typeof body.board_rank).toBe("string");
    expect(body.board_rank).not.toBe(task?.board_rank);
    const headers = (await move?.allHeaders()) ?? {};
    expect(headers["idempotency-key"]).toBeTruthy();
    expect(headers["x-csrf-token"]).toBeTruthy();

    // 12. Dashboard: Today lists the task with its project; the card is on track.
    await page.goto("/");
    const todayPanel = section(page, "Today");
    const item = taskCards(page, TITLE, todayPanel).filter({
      hasText: PROJECT,
    });
    await expect(item).not.toHaveCount(0);
    const projectCardLocator = projectCard(page, projectId);
    await expect(projectCardLocator).toContainText("On track");
    await expect(projectCardLocator.getByTestId("open-count")).toHaveText("1");
    if (!phone) {
      const fits = await page.evaluate(
        () =>
          (document.scrollingElement?.scrollHeight ?? Infinity) <=
          window.innerHeight,
      );
      expect(fits).toBe(true);
    }
  },
);

test.describe("A1.1 quick-add with AI", () => {
  const AI_TITLE = "Send Acme the March invoice";
  const PLACEHOLDER = "Open the invoice template";
  const AGENT_FIRST_ACTION =
    "Open last month's invoice in Wave and duplicate it";

  test(
    "A1.1 label under 1 s, enrichment streams in, one-click override persists",
    { tag: ["@A1.1", "@J2", "@FR-3.3", "@FR-4.1", "@FR-4.4", "@P1-08"] },
    async ({ signedInPage: page, fakes }, testInfo) => {
      test.fail();
      const phone = testInfo.project.name === "phone";
      // Jev answers `hybrid` (0.93) at its recorded p50 of 250 ms; Generation
      // gives the placeholder; the acme-site runner enriches after 1,500 ms.
      await fakes.script("decisions.jev", {
        question: "quick_add_label",
        answer: "hybrid",
        confidence: 0.93,
        latency_ms: 250,
      });
      await fakes.script("generation", { first_action: PLACEHOLDER });
      await fakes.runner.script(
        runnerScript(ACME_AGENT, "enrich", "enrich__hybrid_invoice", 1_500),
      );
      const acme = await projectIdByName(page.request, ACME);
      await page.goto("/");

      // 1 and 2. `/` (the bottom button on a phone), title, typeahead, Enter;
      // the timer starts at the Enter keypress.
      await quickAdd(page, phone, AI_TITLE, { typed: "Acme", name: ACME });
      const enter = Date.now();

      // 3. The label chip reads Hybrid within 1,000 ms of Enter, with a
      // one-line reason on focus.
      const row = quickAddedRow(page, AI_TITLE);
      const chip = labelChip(row);
      await expect(chip).toHaveText("Hybrid", { timeout: 1_000 });
      expect(Date.now() - enter).toBeLessThan(1_000);
      await chip.focus();
      const describedBy = await chip.getAttribute("aria-describedby");
      expect(describedBy).toBeTruthy();
      const reason = page.locator(`[id="${describedBy ?? ""}"]`);
      await expect(reason).toBeVisible();
      const reasonText = (await reason.innerText()).trim();
      expect(reasonText).not.toBe("");
      expect(reasonText).not.toContain("\n");

      // 4. Placeholder first, then the agent's first action; estimate 20 min;
      // at least one acceptance criterion.
      const first = firstAction(row);
      await expect(first).toHaveAttribute("data-state", "placeholder");
      await expect(first).toHaveText(PLACEHOLDER);
      await expect(first).toHaveText(AGENT_FIRST_ACTION, { timeout: 10_000 });
      await expect(first).not.toHaveAttribute("data-state", "placeholder");
      await expect(estimateChip(row)).toHaveText("20 min");
      const [task] = (await listTasks(page.request, acme)).filter(
        (t) => t.title === AI_TITLE,
      );
      expect(task).toBeDefined();
      const taskId = task?.id ?? "";
      await row.getByText(AI_TITLE, { exact: true }).click();
      const drawer = taskDrawer(page, AI_TITLE);
      await expect(
        drawer
          .getByRole("list", { name: "Acceptance criteria" })
          .getByRole("listitem"),
      ).not.toHaveCount(0);
      await page.keyboard.press("Escape");
      await expect(drawer).toBeHidden();

      // 5. One click on Human: the chip changes at once (optimistic) and the
      // PATCH carries the version and an Idempotency-Key.
      const patch = page.waitForRequest(
        (r) =>
          r.method() === "PATCH" &&
          new URL(r.url()).pathname === `/v1/tasks/${taskId}`,
      );
      await chip.click();
      await page.getByRole("option", { name: "Human", exact: true }).click();
      await expect(chip).toHaveText("Human", { timeout: 100 });
      const sent: Request = await patch;
      const body = sent.postDataJSON() as Record<string, unknown>;
      expect(body.label).toBe("human");
      expect(typeof body.version).toBe("number");
      expect((await sent.allHeaders())["idempotency-key"]).toBeTruthy();

      // 6. After a reload the label is still Human and the drawer's history
      // says it came from you.
      await page.reload();
      const reloaded = quickAddedRow(page, AI_TITLE);
      await expect(labelChip(reloaded)).toHaveText("Human");
      await reloaded.getByText(AI_TITLE, { exact: true }).click();
      await expect(labelChip(drawer)).toHaveText("Human");
      await expect(
        drawer
          .getByRole("region", { name: "History" })
          .getByRole("listitem")
          .filter({ hasText: "Label" })
          .first(),
      ).toContainText("you");
    },
  );
});
