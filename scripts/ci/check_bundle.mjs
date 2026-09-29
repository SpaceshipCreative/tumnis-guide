#!/usr/bin/env node
// Bundle budget gate (P0-22, PERF-2): the JavaScript a cold open must download, the
// entry chunks and every chunk they import statically, stays under 200 KB gzipped.
// Chunks reached only through dynamic imports (route components, split by the router
// plugin) do not count.
//
//   node scripts/ci/check_bundle.mjs <dist> [--budget-kb 200]
//
// Reads <dist>/.vite/manifest.json (vite build with `build.manifest: true`). Exit 0 within
// budget, 1 over it, 2 on a missing or malformed manifest.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const KB = 1024;

function parseArgs(argv) {
  const args = { dist: undefined, budgetKb: 200 };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--budget-kb") args.budgetKb = Number(argv[(i += 1)]);
    else args.dist = argv[i];
  }
  if (!args.dist || !Number.isFinite(args.budgetKb)) {
    console.error("usage: check_bundle.mjs <dist> [--budget-kb 200]");
    process.exit(2);
  }
  return args;
}

/** Manifest keys of the entries and everything they import statically. */
export function initialChunks(manifest) {
  const seen = new Set();
  const stack = Object.keys(manifest).filter((key) => manifest[key].isEntry);
  while (stack.length > 0) {
    const key = stack.pop();
    if (seen.has(key)) continue;
    seen.add(key);
    for (const imported of manifest[key]?.imports ?? []) stack.push(imported);
  }
  return [...seen];
}

function main() {
  const { dist, budgetKb } = parseArgs(process.argv.slice(2));
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(join(dist, ".vite", "manifest.json"), "utf8"));
  } catch (error) {
    console.error(`no Vite manifest in ${dist}: ${error.message}`);
    process.exit(2);
  }
  const files = [
    ...new Set(
      initialChunks(manifest)
        .map((key) => manifest[key]?.file)
        .filter((file) => typeof file === "string" && file.endsWith(".js")),
    ),
  ];
  let total = 0;
  for (const file of files.sort()) {
    const size = gzipSync(readFileSync(join(dist, file)), { level: 9 }).length;
    total += size;
    console.log(`${(size / KB).toFixed(1).padStart(8)} KB  ${file}`);
  }
  const verdict = total <= budgetKb * KB ? "within budget" : "OVER BUDGET";
  console.log(
    `${(total / KB).toFixed(1).padStart(8)} KB  initial JavaScript, gzipped ` +
      `(budget ${budgetKb} KB): ${verdict}`,
  );
  process.exit(total <= budgetKb * KB ? 0 : 1);
}

main();
