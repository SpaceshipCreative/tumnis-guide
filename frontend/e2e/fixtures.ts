// Shared Playwright fixtures. Specs import `test` and `expect` from here, never
// from @playwright/test directly, so every spec can ask for these fixtures.
//
// Both fixtures are stubs in P0-02: a spec that requests one fails loudly
// until its work package fills it in. Fixtures are lazy, so specs that do not
// request them (like harness.spec.ts) are unaffected.
import { test as base, type Page } from "@playwright/test";

export { expect } from "@playwright/test";

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
  seededApp: async ({ baseURL }, use) => {
    await use(
      pending("seededApp", "P0-04", `POST ${baseURL ?? ""}/v1/test/reset`),
    );
  },
  signedInPage: async ({ page }, use) => {
    await use(pending("signedInPage", "P0-13", `sign-in from ${page.url()}`));
  },
});
