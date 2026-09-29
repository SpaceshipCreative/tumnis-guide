// Fractional ranking keys (P0-17, FR-2.1): the TypeScript port answers every row of the
// vectors file the Python port is tested on (backend/fixtures/rank/vectors.json).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "vitest";

interface Vector {
  a: string | null;
  b: string | null;
  out?: string;
  error?: boolean;
}

interface RankModule {
  between: (a: string | null, b: string | null) => string;
  RankError: new (...args: never[]) => Error;
}

// Vitest runs from frontend/ (jsdom gives import.meta.url no file scheme).
const VECTORS = resolve(process.cwd(), "../backend/fixtures/rank/vectors.json");

// Loaded at run time, so this file compiles before rank.ts exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

test("[P0-17][FR-2.1] T-P0-17-08 matches shared vectors", async () => {
  const { between, RankError } = await load<RankModule>("./rank");
  const vectors = JSON.parse(readFileSync(VECTORS, "utf8")) as Vector[];
  expect(vectors.length).toBeGreaterThan(10);
  for (const { a, b, out, error } of vectors) {
    if (error) {
      expect(() => between(a, b)).toThrow(RankError);
    } else {
      expect(between(a, b)).toBe(out);
    }
  }
});
