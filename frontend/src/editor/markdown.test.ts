// T-P1-17-05 (ADR-0008): a construct the editor does not model (a Markdown table) makes
// canEditSafely false, so the note opens as Markdown source in a textarea instead of
// being rewritten; every other fixture opens in the rich editor.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import { canEditSafely, editorMode } from "./markdown";

const dir = fileURLToPath(
  new globalThis.URL("../test/fixtures/roundtrip/", import.meta.url).href,
);
const fixture = (name: string) => readFileSync(join(dir, name), "utf8");

test.fails("[P1-17][ADR-0008] unsupported constructs open as source", () => {
  const table = fixture("table-unsupported.md");
  expect(canEditSafely(table)).toBe(false);
  expect(editorMode(table)).toBe("source");

  for (const name of ["headings.md", "tasks-checked.md", "mentions.md"]) {
    const src = fixture(name);
    expect(canEditSafely(src)).toBe(true);
    expect(editorMode(src)).toBe("rich");
  }
});
