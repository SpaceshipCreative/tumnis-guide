#!/usr/bin/env node
// The latency gate (P0-29, PERF-2): every tagged request p95 in the k6 summaries must stay
// under min(NFR, 1.2 x its perf/baseline.json p95). The k6 thresholds apply the same
// rule inside each run; this check reads the exported summaries, so the job fails with
// one table of every latency against its limit.
//
//   node perf/check_summary.mjs [--baseline perf/baseline.json] <summary.json>...
//
// Exit 0 within every limit, 1 over one, 2 on unreadable input or no latency found.
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { limitMs } from "./k6/limits.js";
import { p95ByName, readJson } from "./summary.mjs";

const DEFAULT_BASELINE = join(dirname(fileURLToPath(import.meta.url)), "baseline.json");

function parseArgs(argv) {
  const args = { baseline: DEFAULT_BASELINE, summaries: [] };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--baseline") args.baseline = argv[(i += 1)];
    else args.summaries.push(argv[i]);
  }
  if (!args.baseline || args.summaries.length === 0) {
    console.error("usage: check_summary.mjs [--baseline <baseline.json>] <summary.json>...");
    process.exit(2);
  }
  return args;
}

function main() {
  const { baseline: baselinePath, summaries } = parseArgs(process.argv.slice(2));
  const baseline = readJson(baselinePath);
  let checked = 0;
  let over = 0;
  for (const path of summaries) {
    for (const [name, p95] of Object.entries(p95ByName(readJson(path)))) {
      let limit;
      try {
        limit = limitMs(name, baseline);
      } catch (error) {
        console.error(error.message);
        process.exit(2);
      }
      const ok = p95 < limit;
      checked += 1;
      if (!ok) over += 1;
      const base = baseline[name].p95_ms;
      console.log(
        `${name.padEnd(20)} p95 ${p95.toFixed(1).padStart(7)} ms  limit ${String(limit).padStart(4)} ms` +
          `  (baseline ${String(base)} ms)  ${ok ? "ok" : "OVER"}`,
      );
    }
  }
  if (checked === 0) {
    console.error("no tagged http_req_duration p95 in the summaries");
    process.exit(2);
  }
  process.exit(over === 0 ? 0 : 1);
}

main();
