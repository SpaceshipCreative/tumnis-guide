// Shared Playwright fixtures. Specs import `test` and `expect` from here, never
// from @playwright/test directly, so every spec can ask for these fixtures.
//
// `seededApp` resets the compose.test stack before each test (P0-04); the reset
// also gives the server its real clock back.
// `signedInPage` (P0-13) resets the stack too, then signs in as the seed user
// through the API (password, then the TOTP code of the seed secret at the real
// time: after a reset the stack's clock is real), so the page carries the
// session and CSRF cookies. Fixtures are lazy, so specs that do not request
// them are unaffected.
// `page` keeps the server clock with the browser's (issue #6): when a test fixes
// the page's time (`page.clock.install({ time })`, `setFixedTime`,
// `setSystemTime`), the same instant goes to `POST /v1/test/clock` (fakes only),
// so TOTP checks and "today" on the server match the page. The override lasts
// until the next reset; the fixture clears it after a test that set it.
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
  type Locator,
  type Page,
} from "@playwright/test";

export { expect };

/** The seed sets `POST /v1/test/reset?set=` loads (backend `tumnis.seed.SeedSet`). */
export type SeedSetName = "seed" | "load" | "ten_projects";

/** The compose.test stack reset to the seed set (TUMNIS_ADAPTERS=fake). */
export interface SeededApp {
  readonly baseURL: string;
  /**
   * `POST /v1/test/reset` (`?set=load` for 10 projects and 2,000 tasks, `?set=ten_projects`
   * for the seed plus 7 more projects, P0-23).
   */
  reset(set?: SeedSetName): Promise<void>;
}

interface E2EFixtures {
  /** P0-04: resets the stack through `POST /v1/test/reset` before the test. */
  seededApp: SeededApp;
  /** P0-13: a page signed in as the seed user (TOTP from the seed secret). */
  signedInPage: Page;
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

/** Wraps the page clock's instant-setting calls so the server clock follows. */
function followPageClock(
  page: Page,
  request: APIRequestContext,
): () => boolean {
  const clock = page.clock;
  let synced = false;
  const sync = async (time: ClockTime | undefined): Promise<void> => {
    if (time === undefined) return;
    await setServerClock(request, time);
    synced = true;
  };
  const install = clock.install.bind(clock);
  clock.install = async (options) => {
    await install(options);
    await sync(options?.time);
  };
  const setFixedTime = clock.setFixedTime.bind(clock);
  clock.setFixedTime = async (time) => {
    await setFixedTime(time);
    await sync(time);
  };
  const setSystemTime = clock.setSystemTime.bind(clock);
  clock.setSystemTime = async (time) => {
    await setSystemTime(time);
    await sync(time);
  };
  return () => synced;
}

export const test = base.extend<E2EFixtures>({
  page: async ({ page, request }, use) => {
    const synced = followPageClock(page, request);
    await use(page);
    // The next test starts on the real clock even if it never resets the stack.
    if (synced()) await request.post("/v1/test/reset");
  },
  seededApp: async ({ baseURL, request }, use) => {
    // Mounted only with fake adapters (compose.test and previews).
    const reset = async (set: SeedSetName = "seed"): Promise<void> => {
      const response = await request.post("/v1/test/reset", {
        params: set === "seed" ? {} : { set },
      });
      expect(response.status(), `POST /v1/test/reset (${set})`).toBe(204);
    };
    await reset();
    await use({ baseURL: baseURL ?? "", reset });
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
    await use(page);
  },
});

// --- Seed user and TOTP ------------------------------------------------------

const SEED_WORKSPACE = new URL(
  "../../backend/fixtures/seed/workspace.yaml",
  import.meta.url,
);

export interface SeedUser {
  readonly email: string;
  readonly password: string;
  readonly totpSecret: string;
}

/** The seed user from backend/fixtures/seed/workspace.yaml (the only user). */
export function seedUser(): SeedUser {
  const text = readFileSync(SEED_WORKSPACE, "utf8");
  const field = (pattern: RegExp): string => {
    const value = pattern.exec(text)?.[1];
    if (value === undefined) {
      throw new Error(`seed workspace.yaml has no ${pattern.source}`);
    }
    return value;
  };
  return {
    email: field(/email:\s*([^\s,}]+)/),
    password: field(/password:\s*"([^"]+)"/),
    totpSecret: field(/totp_secret:\s*([A-Z2-7]+)/),
  };
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
  readonly idempotency_key: string | null;
  readonly replayed: boolean;
}

/** `GET /v1/test/requests` (fakes only, P0-10): the last 500 write requests. */
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
  return rows as unknown as RecordedWrite[];
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
  const reset = await request.post("/v1/test/reset", {
    params: set === "seed" ? {} : { set },
  });
  if (reset.status() !== 204) {
    throw new Error(`POST /v1/test/reset -> ${String(reset.status())}`);
  }
  const user = seedUser();
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
