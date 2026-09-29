// Shared Playwright fixtures. Specs import `test` and `expect` from here, never
// from @playwright/test directly, so every spec can ask for these fixtures.
//
// `signedInPage` is a stub until P0-13: a spec that requests it fails loudly.
// Fixtures are lazy, so specs that do not request them are unaffected.
import { test as base, expect, type Page } from "@playwright/test";

export { expect };

/** The compose.test stack reset to the seed set (TUMNIS_ADAPTERS=fake). */
export interface SeededApp {
  readonly baseURL: string;
}

interface E2EFixtures {
  /** P0-04: resets the stack through `POST /v1/test/reset`. */
  seededApp: SeededApp;
  /** P0-13: a page signed in as the seed user (TOTP from the seed secret). */
  signedInPage: Page;
}

function pending(fixture: string, wp: string, detail: string): never {
  throw new Error(`${fixture} is a stub until ${wp} (${detail})`);
}

export const test = base.extend<E2EFixtures>({
  seededApp: async ({ baseURL, request }, use) => {
    // Mounted only with fake adapters (compose.test and previews).
    const response = await request.post("/v1/test/reset");
    expect(response.status(), "POST /v1/test/reset").toBe(204);
    await use({ baseURL: baseURL ?? "" });
  },
  signedInPage: async ({ page }, use) => {
    await use(pending("signedInPage", "P0-13", `sign-in from ${page.url()}`));
  },
});
