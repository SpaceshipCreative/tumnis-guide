// T-P0-22-15 · The bundle budget gate (P0-22, PERF-2): initial JavaScript, the entry and
// everything it imports statically, must stay under 200 KB gzipped; lazy chunks do not
// count. Run: node --test scripts/ci/check_bundle.test.mjs
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const SCRIPT = join(
  dirname(fileURLToPath(import.meta.url)),
  "check_bundle.mjs",
);
const KB = 1024;

// A dist folder with a Vite manifest; `files` maps a path to its size in bytes of
// random (incompressible) content, `manifest` is .vite/manifest.json.
function dist(files, manifest) {
  const root = mkdtempSync(join(tmpdir(), "bundle-"));
  for (const [path, size] of Object.entries(files)) {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), randomBytes(size));
  }
  mkdirSync(join(root, ".vite"), { recursive: true });
  writeFileSync(join(root, ".vite", "manifest.json"), JSON.stringify(manifest));
  return root;
}

function run(root) {
  return spawnSync(process.execPath, [SCRIPT, root], { encoding: "utf8" });
}

test(
  "[P0-22][PERF-2] T-P0-22-15 fails over 200 KB compressed",
  { todo: "spec:P0-22" },
  () => {
    const small = dist(
      { "assets/index-a.js": 150 * KB, "assets/index-a.css": 300 * KB },
      {
        "index.html": {
          file: "assets/index-a.js",
          isEntry: true,
          css: ["assets/index-a.css"],
        },
      },
    );
    const big = dist(
      { "assets/index-b.js": 150 * KB, "assets/vendor-b.js": 60 * KB },
      {
        "index.html": {
          file: "assets/index-b.js",
          isEntry: true,
          imports: ["_vendor-b.js"],
        },
        "_vendor-b.js": { file: "assets/vendor-b.js" },
      },
    );
    const lazy = dist(
      { "assets/index-c.js": 100 * KB, "assets/route-c.js": 500 * KB },
      {
        "index.html": {
          file: "assets/index-c.js",
          isEntry: true,
          dynamicImports: ["src/routes/big.tsx"],
        },
        "src/routes/big.tsx": {
          file: "assets/route-c.js",
          isDynamicEntry: true,
        },
      },
    );

    const results = [small, big, lazy].map(run);

    assert.deepEqual(
      results.map((r) => r.status),
      [0, 1, 0],
      results.map((r) => r.stdout + r.stderr).join("\n---\n"),
    );
    assert.match(results[1].stdout + results[1].stderr, /over budget/i);
  },
);
