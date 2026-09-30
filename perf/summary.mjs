// Reading k6 end-of-test summaries (`k6 run --summary-export <file>`), for
// perf/check_summary.mjs and perf/write_baseline.mjs (P0-29).
import { readFileSync } from "node:fs";

import { NFR_MS } from "./k6/limits.js";

const TAGGED = /^http_req_duration\{name:([a-z_]+)\}$/;

/**
 * `{name: p95_ms}` for every request name with an NFR in a k6 summary export. The
 * trend's values sit on the metric itself in `--summary-export` files and under
 * `values` in `handleSummary` data; both are read.
 */
export function p95ByName(summary) {
  const found = {};
  for (const [key, metric] of Object.entries(summary?.metrics ?? {})) {
    const name = TAGGED.exec(key)?.[1];
    if (name === undefined || !(name in NFR_MS)) continue;
    const p95 = metric?.["p(95)"] ?? metric?.values?.["p(95)"];
    if (typeof p95 === "number") found[name] = p95;
  }
  return found;
}

/** Parsed JSON of `path`; exits 2 with a message when it cannot be read. */
export function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    console.error(`cannot read ${path}: ${error.message}`);
    process.exit(2);
  }
}
