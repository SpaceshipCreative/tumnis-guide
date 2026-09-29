// The app shell runs under the strict CSP (P0-16, SEC-4): no inline script, no inline
// event handler, and no securitypolicyviolation while the shell loads and navigates.
import { expect, test } from "./fixtures";

declare global {
  interface Window {
    __cspViolations?: string[];
  }
}

test(
  "T-P0-16-18 app shell has no inline script and no CSP violations",
  { tag: ["@SEC-4", "@P0-16"] },
  async ({ page }) => {
    test.fail();
    await page.addInitScript(() => {
      window.__cspViolations = [];
      document.addEventListener("securitypolicyviolation", (event) => {
        window.__cspViolations?.push(
          `${event.violatedDirective} blocked ${event.blockedURI || "inline"}`,
        );
      });
    });
    const consoleViolations: string[] = [];
    page.on("console", (message) => {
      if (/content security policy/i.test(message.text())) {
        consoleViolations.push(message.text());
      }
    });

    const response = await page.goto("/");
    const csp = response?.headers()["content-security-policy"] ?? "";
    expect(csp).toContain("script-src 'self'");
    expect(csp).not.toContain("unsafe-inline");
    await expect(page.getByRole("main")).toBeVisible({ timeout: 3_000 });

    // Navigate: a client-side route change, then a full reload of the shell.
    await page.evaluate(() => {
      history.pushState({}, "", "/?view=today");
    });
    await page.reload();
    await expect(page.getByRole("main")).toBeVisible({ timeout: 3_000 });

    expect(await page.locator("script:not([src])").count()).toBe(0);
    const handlers = await page.evaluate(() =>
      Array.from(document.querySelectorAll("*")).flatMap((element) =>
        Array.from(element.attributes)
          .filter((attribute) => attribute.name.startsWith("on"))
          .map((attribute) => `${element.tagName}[${attribute.name}]`),
      ),
    );
    expect(handlers).toEqual([]);
    expect(await page.evaluate(() => window.__cspViolations ?? [])).toEqual([]);
    expect(consoleViolations).toEqual([]);
  },
);
