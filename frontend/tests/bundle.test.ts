// T-P1-17-18 (PERF-2): Tiptap and ProseMirror stay out of the initial chunk graph (the
// entry and every chunk it imports statically, the graph scripts/ci/check_bundle.mjs
// measures against the 200 KB budget); the editor arrives in a chunk that only a dynamic
// import reaches, so it loads on an editing screen. Builds the app in memory with the
// production config (route splitting on, as in the image build).
import { fileURLToPath } from "node:url";

import { build, type Rolldown } from "vite";
import { expect, test } from "vitest";

type Chunk = Rolldown.OutputChunk;

const EDITOR_MODULE = /node_modules\/(@tiptap\/|prosemirror-)/;

async function buildChunks(): Promise<Chunk[]> {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const vitest = process.env.VITEST;
  // vite.config.ts turns route splitting off under Vitest; this build is the real one.
  delete process.env.VITEST;
  try {
    const result = await build({
      root,
      configFile: `${root}/vite.config.ts`,
      logLevel: "silent",
      build: { write: false, minify: false, sourcemap: false },
    });
    const outputs = Array.isArray(result) ? result : [result];
    return outputs
      .flatMap((o) => ("output" in o ? o.output : []))
      .filter((o): o is Chunk => o.type === "chunk");
  } finally {
    process.env.VITEST = vitest;
  }
}

/** The entry chunks and every chunk they import statically, by file name. */
function initialChunks(chunks: Chunk[]): Set<string> {
  const byName = new Map(chunks.map((c) => [c.fileName, c]));
  const seen = new Set<string>();
  const stack = chunks.filter((c) => c.isEntry).map((c) => c.fileName);
  while (stack.length > 0) {
    const name = stack.pop();
    if (name === undefined || seen.has(name)) continue;
    seen.add(name);
    stack.push(...(byName.get(name)?.imports ?? []));
  }
  return seen;
}

test("[P1-17][PERF-2] editor is lazy loaded", async () => {
  const chunks = await buildChunks();
  const initial = initialChunks(chunks);
  expect(initial.size).toBeGreaterThan(0);

  const editorIn = (c: Chunk) =>
    c.moduleIds.some((id) => EDITOR_MODULE.test(id));
  const initialWithEditor = chunks
    .filter((c) => initial.has(c.fileName) && editorIn(c))
    .map((c) => c.fileName);
  expect(initialWithEditor).toEqual([]);

  // The editor is in the build, in a chunk reached only through a dynamic import.
  const lazyWithEditor = chunks.filter(
    (c) => !initial.has(c.fileName) && editorIn(c),
  );
  expect(lazyWithEditor.length).toBeGreaterThan(0);
  const dynamicTargets = new Set(chunks.flatMap((c) => c.dynamicImports));
  const reachedLazily = (name: string, seen = new Set<string>()): boolean => {
    if (dynamicTargets.has(name)) return true;
    if (seen.has(name)) return false;
    seen.add(name);
    return chunks
      .filter((c) => c.imports.includes(name))
      .some((c) => !initial.has(c.fileName) && reachedLazily(c.fileName, seen));
  };
  expect(lazyWithEditor.every((c) => reachedLazily(c.fileName))).toBe(true);
}, 300_000);
