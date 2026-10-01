// Shared Playwright fixtures. Specs import `test` and `expect` from here, never
// from @playwright/test directly, so every spec can ask for these fixtures.
//
// `seededApp` resets the compose.test stack before each test (P0-04); the reset
// also gives the server its real clock back.
// `fakes` (phase 1) wraps the test-only tick and fake-scripting hooks
// (testHooks.ts); it does nothing until a test calls it.
// `signedInPage` (P0-13) resets the stack too, then signs in as the seed user
// through the API (password, then the TOTP code of the seed secret at the real
// time: after a reset the stack's clock is real), so the page carries the
// session and CSRF cookies. Fixtures are lazy, so specs that do not request
// them are unaffected.
// `page` keeps the server clock with the browser's (issue #6): when a test fixes
// the page's time (`page.clock.install({ time })`, `setFixedTime`,
// `setSystemTime`), the same instant goes to `POST /v1/test/clock` (fakes only),
// so TOTP checks and "today" on the server match the page. While the page's time
// flows (`install`, `setSystemTime`), the server clock moves on with it each
// second (`advance_seconds`). The override lasts until the next reset; the
// fixture clears it after a test that set it.
//
// Below the fixtures are helpers the acceptance specs share. Helpers hold no
// assertions: they locate things and read the API, so the work packages that
// build the UI and the routes may adjust them without touching a locked test
// body (spec-guard locks the `test(...)` blocks, not this file).
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";

import {
  test as base,
  expect,
  type APIRequestContext,
  type APIResponse,
  type BrowserContext,
  type Locator,
  type Page,
  type Request,
} from "@playwright/test";

import { testFakes, type TestFakes } from "./testHooks";

export { expect };
export type { TestFakes };

/** The seed sets `POST /v1/test/reset?set=` loads (backend `tumnis.seed.SeedSet`). */
export type SeedSetName = "seed" | "load" | "ten_projects" | "acceptance";

/**
 * The acceptance journeys' Monday (2026-03-09): the acceptance set's dates are offsets from
 * it, so "due today" and the calendar's busy events land on the day the specs plan.
 */
export const ACCEPTANCE_ANCHOR = "2026-03-09";

/** A phase 1 or phase 2 acceptance tag (`@A1.1` to `@A2.7`). */
const ACCEPTANCE_TAG = /^@A[12]\.\d+$/;

/**
 * The set a test's `seededApp` resets to before it runs, chosen by its tags: the phase 1
 * and phase 2 acceptance specs get the acceptance set (Scott decision 37: `Acme site`,
 * `Beta app`, their agents and the tasks the journeys name), every other spec the seed
 * set, which stays as the phase 0 specs pin it.
 */
export function seedSetFor(tags: readonly string[]): SeedSetName {
  return tags.some((tag) => ACCEPTANCE_TAG.test(tag)) ? "acceptance" : "seed";
}

/** The query `POST /v1/test/reset` takes for `set`. */
function resetParams(set: SeedSetName): Record<string, string> {
  if (set === "seed") return {};
  if (set === "acceptance") return { set, anchor: ACCEPTANCE_ANCHOR };
  return { set };
}

/** The compose.test stack reset to the seed set (TUMNIS_ADAPTERS=fake). */
export interface SeededApp {
  readonly baseURL: string;
  /**
   * `POST /v1/test/reset` (`?set=load` for 10 projects and 2,000 tasks, `?set=ten_projects`
   * for the seed plus 7 more projects, P0-23; `?set=acceptance&anchor=2026-03-09` for the
   * acceptance journeys' rows, which the fixture picks for `@A1.x` and `@A2.x` specs).
   */
  reset(set?: SeedSetName): Promise<void>;
}

interface E2EFixtures {
  /** P0-04: resets the stack through `POST /v1/test/reset` before the test. */
  seededApp: SeededApp;
  /** P0-13: a page signed in as the seed user (TOTP from the seed secret). */
  signedInPage: Page;
  /** Phase 1: `POST /v1/test/tick/…` and `/v1/test/fakes/…` on the page's request context. */
  fakes: TestFakes;
}

type ClockTime = number | string | Date;

/** `POST /v1/test/clock`: fixes the server clock at `time` (fakes only). */
export async function setServerClock(
  request: APIRequestContext,
  time: ClockTime,
): Promise<void> {
  const response = await request.post("/v1/test/clock", {
    data: { time: new Date(time).toISOString() },
  });
  if (!response.ok()) {
    throw new Error(`POST /v1/test/clock -> ${String(response.status())}`);
  }
}

const FOLLOW_MS = 1_000;

/** `POST /v1/test/clock` with `advance_seconds`: moves the fixed server clock on. */
async function advanceServerClock(
  request: APIRequestContext,
  seconds: number,
): Promise<void> {
  await request.post("/v1/test/clock", { data: { advance_seconds: seconds } });
}

interface PageClockFollower {
  /** Whether this test fixed the server clock (the fixture resets after it). */
  synced(): boolean;
  /** Stops moving the server clock on (before the fixture's reset). */
  stop(): Promise<void>;
}

/**
 * Wraps the page clock's instant-setting calls so the server clock follows. Playwright's
 * `install({ time })` and `setSystemTime` leave the page's time flowing from that instant,
 * so the server clock flows with it: every second it moves on by the real time that passed.
 * Otherwise the server stands still for the whole test, and time-based state such as the
 * per-principal rate limit (P0-10: burst 50, refilled per second of server time) never
 * refills; a long journey like A0.1 ran out. `setFixedTime` and `pauseAt` stop the page
 * clock, and the server clock stays at their instant.
 */
function followPageClock(
  page: Page,
  request: APIRequestContext,
): PageClockFollower {
  const clock = page.clock;
  let synced = false;
  let timer: ReturnType<typeof setInterval> | undefined;
  let moving: Promise<void> = Promise.resolve();
  let last = 0;

  const stop = async (): Promise<void> => {
    if (timer !== undefined) clearInterval(timer);
    timer = undefined;
    await moving;
  };
  const flow = (): void => {
    last = performance.now();
    timer ??= setInterval(() => {
      const now = performance.now();
      const seconds = (now - last) / 1_000;
      last = now;
      // One advance at a time; a failed one (the stack resetting) is not the test's.
      moving = moving
        .then(() => advanceServerClock(request, seconds))
        .catch(() => undefined);
    }, FOLLOW_MS);
  };
  const sync = async (
    time: ClockTime | undefined,
    flowing: boolean,
  ): Promise<void> => {
    if (time === undefined) return;
    await stop();
    await setServerClock(request, time);
    synced = true;
    if (flowing) flow();
  };
  const install = clock.install.bind(clock);
  clock.install = async (options) => {
    await install(options);
    await sync(options?.time, true);
  };
  const setFixedTime = clock.setFixedTime.bind(clock);
  clock.setFixedTime = async (time) => {
    await setFixedTime(time);
    await sync(time, false);
  };
  const setSystemTime = clock.setSystemTime.bind(clock);
  clock.setSystemTime = async (time) => {
    await setSystemTime(time);
    await sync(time, timer !== undefined);
  };
  const pauseAt = clock.pauseAt.bind(clock);
  clock.pauseAt = async (time) => {
    await pauseAt(time);
    await sync(time, false);
  };
  return { synced: () => synced, stop };
}

const RESET_ATTEMPTS = 3;
const RESET_TIMEOUT_MS = 120_000;

/**
 * `POST /v1/test/reset`, retried after a 409 (issue #56): a later reset superseded this
 * one while it was seeding, so the stack is not in the state this call asked for. Gives up
 * after three attempts and returns the last response; a 409 is never a success.
 */
async function postReset(
  request: APIRequestContext,
  set: SeedSetName,
): Promise<APIResponse> {
  // The load set (2,000 tasks) outlasts the 10 s action timeout on a busy host (P0-29).
  const send = () =>
    request.post("/v1/test/reset", {
      params: resetParams(set),
      timeout: RESET_TIMEOUT_MS,
    });
  let response = await send();
  for (
    let attempt = 1;
    attempt < RESET_ATTEMPTS && response.status() === 409;
    attempt++
  ) {
    await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
    response = await send();
  }
  return response;
}

export const test = base.extend<E2EFixtures>({
  page: async ({ page, request }, use) => {
    const follower = followPageClock(page, request);
    await use(page);
    await follower.stop();
    // The next test starts on the real clock even if it never resets the stack.
    if (follower.synced()) await request.post("/v1/test/reset");
  },
  seededApp: async ({ baseURL, request, context }, use, testInfo) => {
    // Mounted only with fake adapters (compose.test and previews).
    const reset = async (set: SeedSetName = "seed"): Promise<void> => {
      // A reset empties every table, sessions and users included (P0-29): a browser
      // that was signed in signs in again, as the user of the set it now holds.
      const signedIn = (await context.cookies()).some(
        (cookie) => cookie.name === SESSION_COOKIE,
      );
      // Loading a larger set (15-30 s for the load set in CI) is setup, not the test: the
      // test may wait for it as long as the reset request may take, and then gets what it
      // took on top of its own timeout (P0-29).
      const info = base.info();
      const budget = info.timeout;
      const larger = set !== "seed" && budget > 0;
      if (larger) info.setTimeout(budget + RESET_TIMEOUT_MS * RESET_ATTEMPTS);
      const started = Date.now();
      const response = await postReset(request, set);
      if (larger) info.setTimeout(budget + (Date.now() - started));
      expect(response.status(), `POST /v1/test/reset (${set})`).toBe(204);
      if (signedIn) await signInAs(context.request, seedSetUser(set));
    };
    await reset(seedSetFor(testInfo.tags));
    await use({ baseURL: baseURL ?? "", reset });
  },
  fakes: async ({ page }, use) => {
    await use(testFakes(page.request));
  },
  signedInPage: async ({ page, seededApp }, use) => {
    expect(seededApp.baseURL).toBeTruthy(); // the seed user exists, no code used yet
    const user = seedUser();
    const login = await page.request.post("/v1/auth/login", {
      data: { email: user.email, password: user.password },
    });
    expect(login.status(), "POST /v1/auth/login").toBe(200);
    const { preauth } = (await login.json()) as { preauth: string };
    const signedIn = await page.request.post("/v1/auth/totp", {
      data: { preauth, code: totp(user.totpSecret, new Date()) },
    });
    expect(signedIn.status(), "POST /v1/auth/totp").toBe(200);
    // On the app, not about:blank: a spec may ask the page for `navigator.serviceWorker`
    // (A0.2) before it navigates, and that exists only on the app's origin.
    await page.goto("/");
    await use(page);
  },
});

// --- Seed user and TOTP ------------------------------------------------------

/** The session cookie a sign-in sets (backend `tumnis.modules.auth.csrf`). */
export const SESSION_COOKIE = "__Host-tumnis_session";

const SEED_WORKSPACE = new URL(
  "../../backend/fixtures/seed/workspace.yaml",
  import.meta.url,
);
const LOAD_SET = new URL(
  "../../backend/fixtures/load/load.yaml",
  import.meta.url,
);

export interface SeedUser {
  readonly email: string;
  readonly password: string;
  readonly totpSecret: string;
}

/** The first user of a seed file (its `users:` come before its projects). */
function firstUser(file: URL): SeedUser {
  const text = readFileSync(file, "utf8");
  const field = (pattern: RegExp): string => {
    const value = pattern.exec(text)?.[1];
    if (value === undefined) {
      throw new Error(`${file.pathname} has no ${pattern.source}`);
    }
    return value;
  };
  return {
    email: field(/email:\s*([^\s,}]+)/),
    password: field(/password:\s*"?([^"\s,}]+)"?/),
    totpSecret: field(/totp_secret:\s*([A-Z2-7]+)/),
  };
}

/** The seed user from backend/fixtures/seed/workspace.yaml (the only user). */
export function seedUser(): SeedUser {
  return firstUser(SEED_WORKSPACE);
}

/**
 * The user a seed set signs in as: the load set (backend/fixtures/load/load.yaml) has its
 * own workspace and user; the other sets build on the seed workspace.
 */
export function seedSetUser(set: SeedSetName): SeedUser {
  return set === "load" ? firstUser(LOAD_SET) : seedUser();
}

/** Password, then the TOTP code at the real time; the session lands in `request`'s cookies. */
async function signInAs(
  request: APIRequestContext,
  user: SeedUser,
): Promise<void> {
  const login = await request.post("/v1/auth/login", {
    data: { email: user.email, password: user.password },
  });
  if (!login.ok()) {
    throw new Error(`POST /v1/auth/login -> ${String(login.status())}`);
  }
  const { preauth } = (await login.json()) as { preauth: string };
  const code = await request.post("/v1/auth/totp", {
    data: { preauth, code: totp(user.totpSecret, new Date()) },
  });
  if (!code.ok()) {
    throw new Error(`POST /v1/auth/totp -> ${String(code.status())}`);
  }
}

function base32(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/=+$/, "").toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index < 0) throw new Error(`not base32: ${char}`);
    bits += index.toString(2).padStart(5, "0");
  }
  const bytes = bits.match(/.{8}/g) ?? [];
  return Buffer.from(bytes.map((byte) => parseInt(byte, 2)));
}

/** RFC 6238 TOTP (SHA-1, 30 s step, 6 digits) for `secret` at `at`. */
export function totp(secret: string, at: Date): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at.getTime() / 30_000)));
  const digest = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = (digest.at(-1) ?? 0) & 0x0f;
  const code = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, "0");
}

// --- API reads (the session rides on page.request's cookies) ------------------

type Json = Record<string, unknown>;

/** Items of a `Page[T]` response (`{items, next_cursor}`), following cursors. */
async function listAll(
  request: APIRequestContext,
  path: string,
  params: Record<string, string>,
): Promise<Json[]> {
  const items: Json[] = [];
  let cursor: string | null = null;
  do {
    const query: Record<string, string> = cursor
      ? { ...params, cursor }
      : params;
    const response = await request.get(path, { params: query });
    if (!response.ok()) {
      throw new Error(`GET ${path} -> ${String(response.status())}`);
    }
    const body = (await response.json()) as Json;
    const page = Array.isArray(body)
      ? (body as Json[])
      : (body.items as Json[]);
    items.push(...page);
    cursor = Array.isArray(body) ? null : (body.next_cursor as string | null);
  } while (cursor);
  return items;
}

export interface TaskRow {
  readonly id: string;
  readonly title: string;
  readonly version: number;
  readonly column_id: string | null;
  readonly board_rank: string | null;
}

/** `GET /v1/tasks?project_id=<id>`: every task in the project. */
export async function listTasks(
  request: APIRequestContext,
  projectId: string,
): Promise<TaskRow[]> {
  const rows = await listAll(request, "/v1/tasks", { project_id: projectId });
  return rows as unknown as TaskRow[];
}

/** The id of the one project with this exact name. */
export async function projectIdByName(
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const rows = await listAll(request, "/v1/projects", {});
  const hits = rows.filter((row) => row.name === name);
  if (hits.length !== 1) {
    throw new Error(`${String(hits.length)} projects named ${name}`);
  }
  return String(hits[0]?.id);
}

/** Board columns of a project, `{id, name}` in board order. */
export async function boardColumns(
  request: APIRequestContext,
  projectId: string,
): Promise<{ id: string; name: string }[]> {
  const response = await request.get(`/v1/projects/${projectId}/columns`);
  if (!response.ok()) {
    throw new Error(`columns of ${projectId} -> ${String(response.status())}`);
  }
  const body = (await response.json()) as Json | Json[];
  const rows = Array.isArray(body) ? body : (body.items as Json[]);
  return rows.map((row) => ({ id: String(row.id), name: String(row.name) }));
}

export interface RecordedWrite {
  readonly method: string;
  readonly path: string;
  readonly route: string;
  readonly idempotency_key: string | null;
  readonly replayed: boolean;
}

/**
 * `GET /v1/test/requests` (fakes only, P0-10): the write requests of `filter`'s method and
 * path since the stack was last reset (the server answers its last 500 of everything).
 */
export async function recordedWrites(
  request: APIRequestContext,
  filter: { path: string; method: string },
): Promise<RecordedWrite[]> {
  const response = await request.get("/v1/test/requests", { params: filter });
  if (!response.ok()) {
    throw new Error(`GET /v1/test/requests -> ${String(response.status())}`);
  }
  const body = (await response.json()) as Json | Json[];
  const rows = Array.isArray(body) ? body : (body.items as Json[]);
  const all = rows as unknown as RecordedWrite[];
  const reset = all.map((w) => w.route).lastIndexOf("/v1/test/reset");
  return all
    .slice(reset + 1)
    .filter((w) => w.method === filter.method && w.path === filter.path);
}

// --- UI locators and flows -----------------------------------------------------

/** The quick-add dialog. */
export function quickAddDialog(page: Page): Locator {
  return page.getByRole("dialog", { name: "Quick add" });
}

/** Opens quick-add: `/` on a keyboard, the Quick add button on a phone. */
export async function openQuickAdd(page: Page, phone: boolean): Promise<void> {
  if (phone) {
    await page.getByRole("button", { name: "Quick add" }).click();
  } else {
    await page.keyboard.press("/");
  }
}

/** Types a title, picks the project through the typeahead, presses Enter. */
export async function quickAdd(
  page: Page,
  phone: boolean,
  title: string,
  project: { typed: string; name: string },
): Promise<void> {
  await openQuickAdd(page, phone);
  const dialog = quickAddDialog(page);
  await dialog.getByRole("textbox", { name: "Title" }).fill(title);
  await dialog.getByRole("combobox", { name: "Project" }).fill(project.typed);
  await page.getByRole("option", { name: project.name, exact: true }).click();
  await dialog.getByRole("textbox", { name: "Title" }).press("Enter");
}

/** A dashboard project card, found by its project id (names can repeat). */
export function projectCard(page: Page, projectId: string): Locator {
  return page.locator(`[data-project-id="${projectId}"]`);
}

/** A board column or task-list section by its heading. */
export function section(page: Page, name: string): Locator {
  return page.getByRole("region", { name, exact: true });
}

/** Task cards or rows with this exact title, on the page or inside `within`. */
export function taskCards(
  page: Page,
  title: string,
  within?: Locator,
): Locator {
  return (within ?? page).getByRole("listitem").filter({
    has: page.getByText(title, { exact: true }),
  });
}

/**
 * `startTime` of the first performance mark named `name`, waiting for it
 * (same approach as P0-29's timing helpers). A mark that never comes rejects
 * after `timeoutMs`, so the test fails with a reason instead of timing out.
 */
export async function markStartTime(
  page: Page,
  name: string,
  timeoutMs = 10_000,
): Promise<number> {
  return page.evaluate(
    ([markName, limit]) =>
      new Promise<number>((resolve, reject) => {
        const seen = performance.getEntriesByName(markName, "mark")[0];
        if (seen) {
          resolve(seen.startTime);
          return;
        }
        const observer = new PerformanceObserver((list) => {
          const hit = list.getEntries().find((e) => e.name === markName);
          if (hit) {
            observer.disconnect();
            resolve(hit.startTime);
          }
        });
        observer.observe({ type: "mark", buffered: true });
        setTimeout(() => {
          observer.disconnect();
          reject(
            new Error(`no performance mark ${markName} in ${String(limit)} ms`),
          );
        }, limit);
      }),
    [name, timeoutMs] as const,
  );
}

// --- App shell (P0-22) ----------------------------------------------------------

/**
 * Resets the stack to a seed set (the seed by default) and signs the seed user in through
 * the API (the page's cookies carry the session). Throws instead of asserting.
 */
export async function signIn(
  request: APIRequestContext,
  set: SeedSetName = "seed",
): Promise<void> {
  const reset = await postReset(request, set);
  if (reset.status() !== 204) {
    throw new Error(`POST /v1/test/reset -> ${String(reset.status())}`);
  }
  await signInAs(request, seedSetUser(set));
}

/**
 * Signs in (after resetting to `set`), opens the app shell at `path` and waits for `main`
 * and the navigation.
 */
export async function openShell(
  page: Page,
  path = "/",
  set: SeedSetName = "seed",
): Promise<void> {
  await signIn(page.request, set);
  await page.goto(path);
  await page.getByRole("main").waitFor();
  await page
    .getByRole("navigation", { name: "Primary" })
    .filter({ visible: true })
    .waitFor();
}

/** Links in the shell's visible navigation (the rail or the bottom bar). */
export function shellNavLinks(page: Page): Locator {
  return page
    .getByRole("navigation", { name: "Primary" })
    .filter({ visible: true })
    .getByRole("link");
}

/** Whether the element shows a focus indicator: an outline or a box shadow. */
export async function focusRing(element: Locator): Promise<boolean> {
  return element.evaluate((el) => {
    const style = getComputedStyle(el);
    const outline =
      style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 1;
    return outline || style.boxShadow !== "none";
  });
}

// --- Settings (P0-26) --------------------------------------------------------

/** Each Settings section's name in the section list and its primary control. */
export const SETTINGS_SECTIONS: readonly (readonly [
  string,
  (page: Page) => Locator,
])[] = [
  [
    "Account",
    (page) => page.getByRole("button", { name: "Set up a new authenticator" }),
  ],
  [
    "Sessions",
    (page) => page.getByRole("button", { name: "Sign out other devices" }),
  ],
  ["API keys", (page) => page.getByRole("button", { name: "Create key" })],
  ["Audit log", (page) => page.getByRole("link", { name: "Export CSV" })],
  ["Dead letters", (page) => page.getByRole("combobox", { name: "Status" })],
  ["Workspace", (page) => page.getByRole("button", { name: "Save" })],
  ["Working hours", (page) => page.getByRole("button", { name: "Save" })],
];

/**
 * Opens a Settings section through the section list: on a phone the list sits behind
 * the "All settings" back button; on a laptop it is the side navigation.
 */
export async function openSettingsSection(
  page: Page,
  label: string,
): Promise<void> {
  await page.getByRole("heading", { level: 1, name: "Settings" }).waitFor();
  const back = page.getByRole("button", { name: "All settings" });
  if (await back.isVisible()) await back.click();
  await page
    .getByRole("navigation", { name: "Settings sections" })
    .getByRole("link", { name: label, exact: true })
    .click();
  await page.getByRole("heading", { level: 2, name: label }).waitFor();
}

/** Whether the page scrolls sideways. */
export async function scrollsSideways(page: Page): Promise<boolean> {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth >
      document.documentElement.clientWidth,
  );
}

// --- Dashboard (P0-23) ---------------------------------------------------------

/** Every project card on the dashboard. */
export function projectCards(page: Page): Locator {
  return page.locator("[data-project-id]");
}

/** The dashboard project card showing this project name. */
export function projectCardNamed(page: Page, name: string): Locator {
  return projectCards(page).filter({
    has: page.getByRole("link", { name, exact: true }),
  });
}

/** Whether the page scrolls vertically (A0.1's check at 1280 x 800). */
export async function scrollsVertically(page: Page): Promise<boolean> {
  return page.evaluate(
    () =>
      (document.scrollingElement?.scrollHeight ?? Infinity) >
      window.innerHeight,
  );
}

// --- Project page and board (P0-24) ---------------------------------------------

/** The board cards (not their checklist items) inside a column. */
export function boardCards(column: Locator): Locator {
  return column.locator("[data-board-card]");
}

/**
 * Signs in (after resetting to the seed set), opens the project called `name` with
 * `search` (for example `?view=board`) and waits for its header. Answers the project id.
 */
export async function openProject(
  page: Page,
  name: string,
  search = "",
): Promise<string> {
  await signIn(page.request);
  const projectId = await projectIdByName(page.request, name);
  await page.goto(`/projects/${projectId}${search}`);
  await page.getByRole("heading", { level: 1, name }).waitFor();
  return projectId;
}

/** Every write request the page sends from now on whose path matches `pattern`. */
export function recordWrites(page: Page, pattern: RegExp): Request[] {
  const writes: Request[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET" && pattern.test(path)) writes.push(request);
  });
  return writes;
}

// --- Performance timings (P0-29) ---------------------------------------------------

/** Waits until the page's service worker is active (the PWA is installed). */
export async function serviceWorkerActive(page: Page): Promise<boolean> {
  return page.evaluate(() =>
    navigator.serviceWorker.ready.then((r) => r.active !== null),
  );
}

/**
 * A fresh page in `context` (a cold PWA open once the service worker is installed) with
 * `latencyMs` of network latency through CDP (`Network.emulateNetworkConditions`,
 * Chromium only; 50 ms is the plan default for the VPN).
 */
export async function coldPage(
  context: BrowserContext,
  latencyMs = 50,
): Promise<Page> {
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: latencyMs,
    downloadThroughput: -1,
    uploadThroughput: -1,
  });
  return page;
}

/** The median of a non-empty list of numbers. */
export function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1
    ? (sorted[mid] ?? NaN)
    : ((sorted[mid - 1] ?? NaN) + (sorted[mid] ?? NaN)) / 2;
}

/**
 * Presses `/` every 100 ms until the quick-add title field has the focus (or `timeoutMs`
 * passes), then types "x". Answers whether the field reads "x".
 */
export async function quickAddUsable(
  page: Page,
  timeoutMs = 10_000,
): Promise<boolean> {
  const title = quickAddDialog(page).getByRole("textbox", { name: "Title" });
  const focused = async (): Promise<boolean> =>
    (await title.count()) > 0 &&
    (await title.evaluate((el) => el === document.activeElement));
  const deadline = Date.now() + timeoutMs;
  while (!(await focused())) {
    if (Date.now() > deadline) return false;
    await page.keyboard.press("/");
    await page.waitForTimeout(100);
  }
  await page.keyboard.type("x");
  return (await title.inputValue()) === "x";
}
