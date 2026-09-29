// Every write goes through apiWrite (P0-22, REL-2): outside lib/fetch.ts and tests,
// nothing imports a generated write function or mutation from src/api. It stands in
// for the plan's ESLint `no-restricted-imports` rule (eslint.config.js is locked to
// agents), and like a sweep it covers new files and new routes without a list.
import { expect, test } from "vitest";

const SDK = import.meta.glob("../api/sdk.gen.ts", {
  query: "?raw",
  import: "default",
  eager: true,
});
const SOURCES = import.meta.glob(["../**/*.{ts,tsx}", "!../api/**"], {
  query: "?raw",
  import: "default",
  eager: true,
});

const ALLOWED = /(^|\/)(lib\/fetch\.ts|test\/.*|[^/]*\.test\.tsx?)$/;
const IMPORT = /import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+"([^"]+)"/g;

function writeFunctions(sdk: string): Set<string> {
  const names = new Set<string>();
  for (const chunk of sdk.split("export const ").slice(1)) {
    const name = /^(\w+)/.exec(chunk)?.[1];
    if (name && /\)\.(post|put|patch|delete)</.test(chunk)) names.add(name);
  }
  return names;
}

test("[P0-22][REL-2] only lib/fetch.ts imports generated write functions", () => {
  const sdk = Object.values(SDK)[0] ?? "";
  const writes = writeFunctions(sdk);
  expect(writes.size).toBeGreaterThan(0);

  const offenders: string[] = [];
  for (const [path, source] of Object.entries(SOURCES)) {
    if (ALLOWED.test(path)) continue;
    for (const match of source.matchAll(IMPORT)) {
      const [, names = "", from = ""] = match;
      if (!/(^|\/)api(\/|$)/.test(from)) continue;
      for (const raw of names.split(",")) {
        const name = raw
          .replace(/^\s*type\s+/, "")
          .split(/\s+as\s+/)[0]
          ?.trim();
        if (name && (writes.has(name) || name.endsWith("Mutation"))) {
          offenders.push(`${path}: ${name}`);
        }
      }
    }
  }
  expect(offenders).toEqual([]);
});
