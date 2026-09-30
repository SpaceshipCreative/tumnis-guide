// Helpers shared by the phase 2 acceptance specs (A2.1 J3, A2.2 UI path, A2.6 J8),
// committed red with them. Like the helpers in fixtures.ts and phase1.ts they hold no
// assertions: they locate things, read the API and arrange state, so the work packages
// that build the UI and the routes may adjust them without touching a locked test body.
// Names follow the plan: `POST /v1/tasks/{id}/run`, `GET /v1/tasks/{id}/comments`,
// `GET /v1/review/count` (P2-04, P1-13), `PUT /v1/focus/level` and the `focus-wake` tick
// (P2-15); the fake runner's `script(taskTitle, runs)` and `lastPacket()` hooks (R-37).
// Seed names come from the plan's phase 2 preconditions: project `Acme site` with the
// AI task `Fix footer link` (a first action, acceptance criteria, one linked tainted
// email and a brief), and the task `Write proposal` blocked 10:00 to 10:50 local.
import { readFileSync } from "node:fs";

import type { APIRequestContext, Locator, Page } from "@playwright/test";
import { Ajv2020 } from "ajv/dist/2020.js";

import {
  listTasks,
  projectIdByName,
  setServerClock,
  type TestFakes,
} from "./fixtures";
import {
  MASTER,
  getPlan,
  getTask,
  postJson,
  runnerScript,
  type TaskDetail,
} from "./phase1";

type Json = Record<string, unknown>;

export const FIX_FOOTER = "Fix footer link";
export const WRITE_PROPOSAL = "Write proposal";
export const FEEDBACK = "Link should open in a new tab";

// --- The packet schema -------------------------------------------------------------

const TASK_PACKET = new URL(
  "../../schemas/packet/v1/task_packet.json",
  import.meta.url,
);

/**
 * The errors of `packet` against schemas/packet/v1/task_packet.json (draft 2020-12,
 * formats as annotations, as the backend's jsonschema check reads them); empty when
 * it validates.
 */
export function packetErrors(packet: unknown): string[] {
  const ajv = new Ajv2020({
    strict: false,
    allErrors: true,
    validateFormats: false,
  });
  const validate = ajv.compile(
    JSON.parse(readFileSync(TASK_PACKET, "utf8")) as Json,
  );
  if (validate(packet)) return [];
  return (validate.errors ?? []).map(
    (e) => `${e.instancePath || "/"} ${e.message ?? e.keyword}`,
  );
}

/** How many `<untrusted-data` blocks open in a packet's prompt text (P2-02). */
export function untrustedBlocks(promptText: string): number {
  return promptText.split("<untrusted-data").length - 1;
}

// --- Fake runner scripts -----------------------------------------------------------

const FOOTER_RESULT = {
  outcome: "done",
  files_touched: [{ path: "src/footer.tsx", change: "modified" }],
  links: [
    {
      kind: "branch",
      url: "https://git.example.com/acme/site/tree/fix-footer-link",
      label: "fix-footer-link",
    },
    {
      kind: "pull_request",
      url: "https://git.example.com/acme/site/pull/7",
      label: "Pull request 7",
    },
  ],
};

/** The three stream lines A2.1's first run sends, in order. */
export const FOOTER_LINES = [
  "Reading the footer component",
  "git.checkout",
  "src/footer.tsx",
] as const;

/**
 * A2.1's script for `Fix footer link`: the first run streams a log line, a tool call
 * and a touched file, uploads `notes.md` (200 bytes), then posts a result; the second
 * run (after the reject) posts a new result.
 */
export const FOOTER_RUNS: readonly (readonly Json[])[] = [
  [
    { stream: { kind: "log", text: FOOTER_LINES[0] } },
    { stream: { kind: "tool_call", text: FOOTER_LINES[1] } },
    { stream: { kind: "file_touched", text: FOOTER_LINES[2] } },
    {
      upload_artifact: {
        name: "notes.md",
        media_type: "text/markdown",
        content: "# Footer notes\n".padEnd(200, "."),
      },
    },
    { result: { ...FOOTER_RESULT, summary: "Fixed the footer link target" } },
  ],
  [
    {
      result: {
        ...FOOTER_RESULT,
        summary: "The footer link now opens in a new tab",
      },
    },
  ],
];

export const QUESTION = "Which footer color?";

/**
 * A2.2's script: one log line, `ask_human` ("Which footer color?"), wait for the
 * answer, then a result that echoes it.
 */
export const QUESTION_RUNS: readonly (readonly Json[])[] = [
  [
    { stream: { kind: "log", text: "Reading the footer component" } },
    { ask_human: { prompt: QUESTION } },
    { result: { ...FOOTER_RESULT, summary: "Footer colour set to {answer}" } },
  ],
];

// --- API reads and writes ----------------------------------------------------------

export interface TaskInfo {
  readonly id: string;
  readonly title: string;
  readonly status: string;
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

/** The one task of `projectName` titled `title`. */
export async function taskByTitle(
  request: APIRequestContext,
  projectName: string,
  title: string,
): Promise<TaskInfo> {
  const projectId = await projectIdByName(request, projectName);
  const hits = (await listTasks(request, projectId)).filter(
    (t) => t.title === title,
  );
  if (hits.length !== 1) {
    throw new Error(`${String(hits.length)} tasks named ${title}`);
  }
  return hits[0] as unknown as TaskInfo;
}

/** `GET /v1/tasks/{id}`: the task's status. */
export async function taskStatus(
  request: APIRequestContext,
  taskId: string,
): Promise<string> {
  return (await getJson<TaskInfo>(request, `/v1/tasks/${taskId}`)).status;
}

/** `GET /v1/tasks/{id}/comments`: the comment bodies, oldest first. */
export async function commentBodies(
  request: APIRequestContext,
  taskId: string,
): Promise<string[]> {
  const body = await getJson<Json | Json[]>(
    request,
    `/v1/tasks/${taskId}/comments`,
  );
  const rows = Array.isArray(body) ? body : (body.items as Json[]);
  return rows.map((row) => String(row.body_md));
}

/** `GET /v1/review/count`: the review badge's number. */
export async function reviewCount(request: APIRequestContext): Promise<number> {
  return (await getJson<{ count: number }>(request, "/v1/review/count")).count;
}

/** `POST /v1/tasks/{id}/run` as the user; the run id. */
export async function runTask(
  request: APIRequestContext,
  taskId: string,
): Promise<string> {
  const body = await postJson(request, `/v1/tasks/${taskId}/run`);
  return String(body.run_id);
}

// --- Locators ----------------------------------------------------------------------

/** The dashboard's review badge (a link to /review with the open count). */
export function reviewBadge(page: Page): Locator {
  return page.getByRole("link", { name: /^Review/ });
}

/** The count inside the review badge. */
export function reviewBadgeCount(page: Page): Locator {
  return reviewBadge(page).getByTestId("review-count");
}

/** A review queue item by the title of its task (or its question text). */
export function reviewItem(page: Page, text: string): Locator {
  return page.getByRole("main").getByRole("listitem").filter({ hasText: text });
}

/** The run view's log (inside the task drawer). */
export function runLog(scope: Locator): Locator {
  return scope.getByRole("log", { name: "Run log" });
}

/** The run view's elapsed time. */
export function runElapsed(scope: Locator): Locator {
  return scope.getByTestId("run-elapsed");
}

/** The run view's (or result item's) files touched. */
export function filesTouched(scope: Locator): Locator {
  return scope.getByRole("region", { name: "Files touched" });
}

/** The focus bar (P2-15), on every screen. */
export function focusBar(page: Page): Locator {
  return page.getByRole("region", { name: "Focus" });
}

/** The focus bar's messages, oldest first, each with its attribution. */
export function focusMessages(page: Page): Locator {
  return focusBar(page).getByTestId("focus-message");
}

/** Focus messages whose attribution names this event kind (`Nudge · block_start`). */
export function focusMessagesOf(page: Page, kind: string): Locator {
  return focusMessages(page).filter({
    has: page.getByTestId("focus-attribution").filter({ hasText: kind }),
  });
}

/** The focus bar's current level (after "Less of this", today's lowered level). */
export function focusLevel(page: Page): Locator {
  return focusBar(page).getByTestId("focus-level");
}

// --- Focus (A2.6) --------------------------------------------------------------------

/** 09:50 in America/New_York on Tuesday 10 March 2026. */
export const FOCUS_START = new Date("2026-03-10T13:50:00Z");

/** `hh:mm` local (America/New_York, after the DST change: UTC-4) on `day`. */
export function localTime(day: string, hhmm: string): Date {
  return new Date(`${day}T${hhmm}:00-04:00`);
}

/** `PUT /v1/focus/level`: the workspace's focus level. */
export async function setFocusLevel(
  request: APIRequestContext,
  level: "quiet" | "nudge" | "coach" | "guardrail",
): Promise<void> {
  const { cookies } = await request.storageState();
  const csrf = cookies.find((c) => c.name === "__Host-tumnis_csrf")?.value;
  const headers: Record<string, string> = {
    "Idempotency-Key": `e2e-${crypto.randomUUID()}`,
  };
  if (csrf !== undefined) headers["X-CSRF-Token"] = csrf;
  const response = await request.put("/v1/focus/level", {
    data: { level },
    headers,
  });
  if (!response.ok()) {
    throw new Error(`PUT /v1/focus/level -> ${String(response.status())}`);
  }
}

/**
 * The plan for `day` (the server clock at 08:30 local that day) with `Write proposal`
 * blocked 10:00 to 10:50 local: the master is scripted with the block and one
 * `planner-tick` publishes it (the project keeps the default 25-minute cadence).
 * Answers the task.
 */
export async function publishProposalPlan(
  request: APIRequestContext,
  fakes: TestFakes,
  day: string,
): Promise<TaskDetail> {
  await fakes.runner.script(
    runnerScript(MASTER, "plan", "plan__write_proposal_block"),
  );
  await fakes.tick("planner-tick");
  const plan = await getPlan(request, day);
  const tasks = await Promise.all(
    plan.items.map((item) => getTask(request, item.task_id)),
  );
  const found = tasks.find((t) => t.title === WRITE_PROPOSAL);
  if (!found) throw new Error(`the ${day} plan has no ${WRITE_PROPOSAL}`);
  return found;
}

/**
 * Moves both clocks to `to`: the server clock (`POST /v1/test/clock`), one
 * `focus-wake` tick so waiting focus workflows look at the new time, then the page's
 * clock by the same step (`page.clock.fastForward`).
 */
export async function advanceBothClocks(
  page: Page,
  fakes: TestFakes,
  from: Date,
  to: Date,
): Promise<Date> {
  await setServerClock(page.request, to);
  await fakes.tick("focus-wake");
  await page.clock.fastForward(to.getTime() - from.getTime());
  return to;
}

/** `minutes` after `at`. */
export function plus(at: Date, minutes: number): Date {
  return new Date(at.getTime() + minutes * 60_000);
}
