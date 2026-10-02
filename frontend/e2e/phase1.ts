// Helpers shared by the phase 1 acceptance specs (A1.1 to A1.6), committed red
// with them. Like the helpers in fixtures.ts they hold no assertions: they
// locate things, read the API and arrange state, so the work packages that
// build the UI and the routes may adjust them without touching a locked test
// body. Route names follow the plan: `GET /v1/plan/{day}` and its `calendar`,
// `alternates` and item actions (P1-10, P1-11), `GET /v1/tasks/{id}` (P0-18).
// Seed names come from the plan's phase 1 seed additions: project `Acme site`
// with the agent profile `acme-site`, the master `tumnis-master` (P1-04, P1-06),
// the Human task `Write Acme proposal` of 90 minutes (P1-11).
import { readFileSync } from "node:fs";

import type { APIRequestContext, Locator, Page } from "@playwright/test";

import {
  listTasks,
  projectIdByName,
  section,
  setServerClock,
  taskCards,
  type TestFakes,
} from "./fixtures";

type Json = Record<string, unknown>;

/** Monday 2026-03-09, the first weekday after the US DST change. */
export const MONDAY = "2026-03-09";
/** 08:30 in America/New_York on MONDAY: the default plan time. */
export const MONDAY_PLAN_TIME = new Date("2026-03-09T12:30:00Z");
export const ACME = "Acme site";
export const ACME_AGENT = "acme-site";
export const MASTER = "tumnis-master";

// --- API reads -----------------------------------------------------------------

export interface Interval {
  readonly start: string;
  readonly end: string;
}

export interface PlanItem {
  readonly task_id: string;
  readonly position: number;
  readonly reason: string;
  readonly block: Interval | null;
  readonly accepted_at: string | null;
  readonly removed_at: string | null;
}

export interface PlanIssue {
  readonly id: string;
  readonly task_id: string;
  readonly kind: string;
  readonly offer: { split: number[] | null; move_to: string | null };
  readonly review_item_id: string | null;
  readonly resolved_at: string | null;
}

export interface Plan {
  readonly id: string;
  readonly day: string;
  readonly source: "master" | "fallback" | "manual";
  readonly status: "published" | "superseded";
  readonly trigger: "morning" | "replan" | "manual";
  readonly notice: string | null;
  readonly items: PlanItem[];
  readonly issues: PlanIssue[];
}

export interface DayCalendar {
  readonly timezone: string;
  readonly free_blocks: (Interval & { minutes: number })[];
}

export interface TaskDetail {
  readonly id: string;
  readonly project_id: string;
  readonly parent_id: string | null;
  readonly title: string;
  readonly label: "human" | "ai" | "hybrid" | null;
  readonly label_source: string | null;
  readonly status: string;
  readonly estimate_minutes: number | null;
  readonly first_action: string | null;
  readonly acceptance_criteria: string | null;
  readonly version: number;
}

async function getJson<T>(
  request: APIRequestContext,
  path: string,
): Promise<T> {
  const response = await request.get(path);
  if (!response.ok()) {
    throw new Error(`GET ${path} -> ${String(response.status())}`);
  }
  return (await response.json()) as T;
}

/**
 * A session write through the API: the CSRF double-submit header from the
 * `__Host-tumnis_csrf` cookie and a fresh `Idempotency-Key` (R-18).
 */
export async function postJson(
  request: APIRequestContext,
  path: string,
  data: Json = {},
): Promise<Json> {
  return sendJson(request, "POST", path, data);
}

async function sendJson(
  request: APIRequestContext,
  method: "POST" | "PUT",
  path: string,
  data: Json,
): Promise<Json> {
  const { cookies } = await request.storageState();
  const csrf = cookies.find((c) => c.name === "__Host-tumnis_csrf")?.value;
  const headers: Record<string, string> = {
    "Idempotency-Key": `e2e-${crypto.randomUUID()}`,
  };
  if (csrf !== undefined) headers["X-CSRF-Token"] = csrf;
  const response = await request.fetch(path, { method, data, headers });
  if (!response.ok()) {
    throw new Error(`${method} ${path} -> ${String(response.status())}`);
  }
  const text = await response.text();
  return text ? (JSON.parse(text) as Json) : {};
}

/** `GET /v1/plan/{day}`: the published plan, its items and issues. */
export function getPlan(
  request: APIRequestContext,
  day: string = MONDAY,
): Promise<Plan> {
  return getJson<Plan>(request, `/v1/plan/${day}`);
}

/** `GET /v1/plan/{day}/calendar`: window, events and free blocks (P1-10). */
export function getDayCalendar(
  request: APIRequestContext,
  day: string = MONDAY,
): Promise<DayCalendar> {
  return getJson<DayCalendar>(request, `/v1/plan/${day}/calendar`);
}

/** `GET /v1/tasks/{id}`. */
export function getTask(
  request: APIRequestContext,
  taskId: string,
): Promise<TaskDetail> {
  return getJson<TaskDetail>(request, `/v1/tasks/${taskId}`);
}

/** Every task of the named project, with the fields the phase 1 specs read. */
export async function tasksOfProject(
  request: APIRequestContext,
  projectName: string,
): Promise<TaskDetail[]> {
  const projectId = await projectIdByName(request, projectName);
  return (await listTasks(request, projectId)) as unknown as TaskDetail[];
}

/** `POST /v1/tasks` in the named project; returns the new task. */
export async function createTask(
  request: APIRequestContext,
  projectName: string,
  title: string,
): Promise<TaskDetail> {
  const projectId = await projectIdByName(request, projectName);
  const body = await postJson(request, "/v1/tasks", {
    project_id: projectId,
    title,
  });
  return body as unknown as TaskDetail;
}

/** Whether `block` lies inside one of `free`. */
export function insideFreeBlock(block: Interval, free: Interval[]): boolean {
  const start = Date.parse(block.start);
  const end = Date.parse(block.end);
  return free.some(
    (f) => Date.parse(f.start) <= start && end <= Date.parse(f.end),
  );
}

/** Length of an interval in minutes. */
export function minutesOf(block: Interval): number {
  return Math.round((Date.parse(block.end) - Date.parse(block.start)) / 60_000);
}

// --- Arrangement -----------------------------------------------------------------

/** A scripted runner result, by name (`recordings/runner/<name>.result.json`). */
export function runnerScript(
  profile: string,
  skill: string,
  result: string,
  delayMs = 0,
): Json {
  return { profile, skill, result: `${result}.result.json`, delay_ms: delayMs };
}

/**
 * Server clock to 08:30 on MONDAY, the master scripted with `script`, one
 * `planner-tick`; returns the published plan.
 */
export async function publishMondayPlan(
  request: APIRequestContext,
  fakes: TestFakes,
  script = "plan__monday_four_picks",
): Promise<Plan> {
  await fakes.runner.script(runnerScript(MASTER, "plan", script));
  await setServerClock(request, MONDAY_PLAN_TIME);
  await fakes.tick("planner-tick");
  return getPlan(request, MONDAY);
}

/**
 * A1.3's calendar: extra busy events on the fake Google accounts so the
 * largest free block on MONDAY is 60 minutes and Tuesday 10 March has a free
 * block of 120 minutes, then one calendar sync. The scenario
 * (backend calendar recordings, `scenarios/no_ninety_minute_gap.json`) is
 * stored first, the two fake accounts are connected the way a person connects
 * them (P1-09: OAuth client in Settings, consent, callback), then the
 * `calendar-sync` test tick syncs them and answers when the syncs end.
 */
export async function squeezeMondayCalendar(fakes: TestFakes): Promise<void> {
  await fakes.script("calendar.google", { scenario: "no_ninety_minute_gap" });
  await connectFakeGoogleAccounts(fakes.request);
  await fakes.tick("calendar-sync");
}

const CONNECT_WAIT_MS = 20_000;
const CONNECT_POLL_MS = 500;

/**
 * Connects the fake Google accounts `a` and `b` (codes `code-a`, `code-b`)
 * through the real OAuth routes, and waits until the worker's exchange has
 * left both connected.
 */
async function connectFakeGoogleAccounts(
  request: APIRequestContext,
): Promise<void> {
  await sendJson(request, "PUT", "/v1/settings/calendar.google", {
    values: { client_id: "e2e-client.example.test", client_secret: "e2e" },
    version: null,
  });
  for (const code of ["code-a", "code-b"]) {
    const { url } = await getJson<{ url: string }>(
      request,
      "/v1/calendar/oauth/start",
    );
    const state = new URL(url).searchParams.get("state") ?? "";
    const query = new URLSearchParams({ state, code }).toString();
    const callback = await request.get(`/v1/calendar/oauth/callback?${query}`, {
      maxRedirects: 0,
    });
    if (callback.status() !== 302) {
      throw new Error(`OAuth callback ${code} -> ${String(callback.status())}`);
    }
  }
  const deadline = Date.now() + CONNECT_WAIT_MS;
  for (;;) {
    const accounts = await getJson<{ status: string }[]>(
      request,
      "/v1/calendar/accounts",
    );
    if (accounts.filter((a) => a.status === "connected").length === 2) return;
    if (Date.now() > deadline) {
      throw new Error("the fake Google accounts did not connect");
    }
    await new Promise((resolve) => setTimeout(resolve, CONNECT_POLL_MS));
  }
}

async function walk(
  request: APIRequestContext,
  taskId: string,
  statuses: string[],
): Promise<void> {
  for (const to of statuses) {
    const { version } = await getTask(request, taskId);
    await postJson(request, `/v1/tasks/${taskId}/status`, { to, version });
  }
}

const RESULT_WAIT_MS = 20_000;
const RESULT_POLL_MS = 1_000;

/**
 * The AI task's result, the way an agent posts it (FR-5.8: only an agent moves
 * work to In review): the fake runner is scripted to post one result for the
 * task (P2-04), the person starts the run (Today to In progress), and the task
 * reaches In review.
 */
async function agentPostsResult(
  request: APIRequestContext,
  fakes: TestFakes,
  task: TaskDetail,
): Promise<void> {
  await fakes.runner.script(task.title, [
    [
      {
        result: {
          outcome: "done",
          summary: "The March analytics report is ready",
          files_touched: [],
          links: [
            {
              kind: "url",
              url: "https://example.test/runs/monday-report",
              label: "Agent result",
            },
          ],
        },
      },
    ],
  ]);
  await postJson(request, `/v1/tasks/${task.id}/run`);
  await waitForTask(
    request,
    task.id,
    (t) => t.status === "in_review",
    `${task.title} did not reach In review`,
  );
}

/**
 * Polls `GET /v1/tasks/{id}` until `done(task)`, at most RESULT_WAIT_MS. The
 * server clock stands still at the plan time; moving it on with the wait
 * refills the per-principal rate limit (P0-10) the polling spends.
 */
async function waitForTask(
  request: APIRequestContext,
  taskId: string,
  done: (task: TaskDetail & { enrichment_status?: string }) => boolean,
  failure: string,
): Promise<void> {
  const deadline = Date.now() + RESULT_WAIT_MS;
  while (!done(await getTask(request, taskId))) {
    if (Date.now() > deadline) throw new Error(failure);
    await new Promise((resolve) => setTimeout(resolve, RESULT_POLL_MS));
    await request.post("/v1/test/clock", {
      data: { advance_seconds: RESULT_POLL_MS / 1_000 },
    });
  }
}

/**
 * A1.6's day: the Monday plan published and accepted (4 items, one AI); one
 * Human item and the AI item Done (the AI one through a run whose result,
 * with its agent result link, the person accepts); the other two still Today;
 * one enrichment run finished today. Returns the tasks by role.
 */
export async function arrangeCloseTheDay(
  request: APIRequestContext,
  fakes: TestFakes,
): Promise<{ shipped: TaskDetail[]; rolling: TaskDetail[] }> {
  const plan = await publishMondayPlan(request, fakes);
  await postJson(request, `/v1/plan/${MONDAY}/accept-all`);
  const tasks = await Promise.all(
    plan.items.map((item) => getTask(request, item.task_id)),
  );
  const ai = tasks.find((t) => t.label === "ai");
  const human = tasks.find((t) => t.label === "human");
  if (!ai || !human)
    throw new Error("the Monday plan needs a Human and an AI item");
  await agentPostsResult(request, fakes, ai);
  await walk(request, ai.id, ["done"]);
  await walk(request, human.id, ["in_progress", "done"]);

  // The enrichment recording answers for a Hybrid task (estimate and split),
  // so Jev labels the new task Hybrid first, as in A1.1; the run then finishes
  // within the day.
  await fakes.script("decisions.jev", {
    question: "quick_add_label",
    answer: "hybrid",
    confidence: 0.93,
  });
  await fakes.runner.script(
    runnerScript(ACME_AGENT, "enrich", "enrich__hybrid_invoice"),
  );
  const invoice = await createTask(
    request,
    ACME,
    "Send Acme the March invoice",
  );
  await waitForTask(
    request,
    invoice.id,
    (t) => t.enrichment_status === "done",
    `${invoice.title} was not enriched`,
  );
  const rolling = tasks.filter((t) => t.id !== ai.id && t.id !== human.id);
  return { shipped: [human, ai], rolling };
}

// --- Locators --------------------------------------------------------------------

/** Items of the dashboard's Today panel. */
export function todayItems(page: Page): Locator {
  return section(page, "Today").getByRole("listitem");
}

/** The row a quick-added task shows up in after Enter (on the current page). */
export function quickAddedRow(page: Page, title: string): Locator {
  return taskCards(page, title).first();
}

/** The label chip of a task row or plan item. */
export function labelChip(row: Locator): Locator {
  return row.getByTestId("label-chip");
}

/** The first-action line (states: placeholder, pending, agent, user). */
export function firstAction(row: Locator): Locator {
  return row.getByTestId("first-action");
}

/** The estimate chip (Human and Hybrid only). */
export function estimateChip(row: Locator): Locator {
  return row.getByTestId("estimate-chip");
}

/** A plan item's time range (`09:00–10:00`), or `Runs anytime` for AI. */
export function planBlock(row: Locator): Locator {
  return row.getByTestId("plan-block");
}

/** A plan item's one-line reason. */
export function planReason(row: Locator): Locator {
  return row.getByTestId("plan-reason");
}

/** The project name shown on a plan item. */
export function planProject(row: Locator): Locator {
  return row.getByTestId("plan-project");
}

/** Highlighted free blocks on the dashboard's calendar strip. */
export function stripFreeBlocks(page: Page): Locator {
  return page.getByTestId("calendar-strip").getByTestId("free-block");
}

/** The open task drawer for this task title. */
export function taskDrawer(page: Page, title: string): Locator {
  return page.getByRole("dialog", { name: title });
}

/** The `Doesn't fit today` row of a plan issue, by task title. */
export function fitOfferRow(page: Page, title: string): Locator {
  return section(page, "Doesn't fit today")
    .getByRole("listitem")
    .filter({ hasText: title });
}

/** The Close the day panel. */
export function closeDayPanel(page: Page): Locator {
  return page.getByRole("dialog", { name: "Close the day" });
}

/** The project page's Knowledge rail section. */
export function knowledgeRail(page: Page): Locator {
  return page.getByRole("region", { name: "Knowledge" });
}

/** The project composer (the drop target for files). */
export function projectComposer(page: Page): Locator {
  return page.getByRole("form", { name: "Composer" });
}

const EXTRACTION = new URL(
  "../../backend/fixtures/extraction/",
  import.meta.url,
);

/**
 * Drops a file from backend/fixtures/extraction on `target`, the way a
 * browser drag from the desktop does (dragenter, dragover, drop with a
 * DataTransfer holding the file).
 */
export async function dropFixture(
  target: Locator,
  fixture: string,
  mimeType: string,
): Promise<void> {
  const bytes = readFileSync(new URL(fixture, EXTRACTION)).toString("base64");
  const transfer = await target.page().evaluateHandle(
    ({ name, type, data }) => {
      const raw = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([raw], name, { type }));
      return dt;
    },
    { name: fixture, type: mimeType, data: bytes },
  );
  for (const type of ["dragenter", "dragover", "drop"]) {
    await target.dispatchEvent(type, { dataTransfer: transfer });
  }
}
