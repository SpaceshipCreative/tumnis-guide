// T-P1-17-01 (ADR-0008): every fixture loads into the editor and saves back to the same
// canonical Markdown, byte for byte; a second round trip changes nothing; frontmatter
// comes back untouched. A fixture with a `.canonical.md` twin specifies its canonical
// form; one with a `.skip-roundtrip` sibling is checked through canEditSafely instead
// (T-P1-17-05).
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

import { roundTrip, splitFrontmatter } from "./markdown";

// A path, not a URL: under jsdom `URL` is jsdom's, which node:fs does not take.
const dir = fileURLToPath(
  new globalThis.URL("../test/fixtures/roundtrip/", import.meta.url).href,
);
const cases = readdirSync(dir).filter(
  (f) =>
    f.endsWith(".md") &&
    !f.endsWith(".canonical.md") &&
    !existsSync(join(dir, f.replace(/\.md$/, ".skip-roundtrip"))),
);

describe.each(cases)("[P1-17][ADR-0008] round trip %s", (name) => {
  const src = readFileSync(join(dir, name), "utf8");
  const canon = join(dir, name.replace(/\.md$/, ".canonical.md"));
  const expected = existsSync(canon) ? readFileSync(canon, "utf8") : src;
  test("loads and saves to canonical markdown", () => {
    expect(roundTrip(src)).toBe(expected);
  });
  test("is idempotent", () => {
    expect(roundTrip(roundTrip(src))).toBe(roundTrip(src));
  });
  test("keeps frontmatter byte for byte", () => {
    expect(splitFrontmatter(roundTrip(src)).frontmatter).toBe(
      splitFrontmatter(src).frontmatter,
    );
  });
});
