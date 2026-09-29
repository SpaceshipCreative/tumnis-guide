// The shell never builds code from strings (issue #36, P0-16 CSP, SEC-4): zod probes
// `new Function` when it builds an object schema unless it is already jitless, and the
// strict CSP reports that probe as a violation even though zod catches the error. In the
// production build the generated schemas ran before main.tsx set zod jitless.
import { expect, test } from "./fixtures";

declare global {
  interface Window {
    __functionCalls?: string[];
  }
}

test(
  "issue 36: no Function built from a string while the shell loads (zod stays jitless)",
  { tag: ["@SEC-4", "@P0-16"] },
  async ({ page }) => {
    await page.addInitScript(() => {
      const calls: string[] = [];
      window.__functionCalls = calls;
      const record = (args: unknown[]) => {
        const where = (new Error().stack ?? "")
          .split("\n")
          .slice(2, 4)
          .join(" | ");
        calls.push(`Function(${args.map(String).join(", ")}) at ${where}`);
      };
      window.Function = new Proxy(Function, {
        apply(target, thisArg, args: unknown[]) {
          record(args);
          return Reflect.apply(target, thisArg, args) as unknown;
        },
        construct(target, args: unknown[], newTarget) {
          record(args);
          return Reflect.construct(target, args, newTarget) as object;
        },
      });
    });

    await page.goto("/");
    await expect(page.getByRole("main")).toBeVisible({ timeout: 3_000 });
    await page.reload();
    await expect(page.getByRole("main")).toBeVisible({ timeout: 3_000 });

    expect(await page.evaluate(() => window.__functionCalls ?? [])).toEqual([]);
  },
);
