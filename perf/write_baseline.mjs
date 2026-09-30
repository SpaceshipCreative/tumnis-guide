#!/usr/bin/env node
// Writes perf/baseline.json from k6 summary exports (P0-29, `make perf-baseline`): each
// request name's p95 is the highest across the summaries given, so a baseline drawn from
// several runs of a noisy runner leans slow rather than fast. A new baseline is a reviewed
// change in a PR labeled `perf-baseline`, never an automatic update.
//
//   node perf/write_baseline.mjs --measured-on <runner> --commit <sha> [--out <file>] <summary.json>...
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { NFR_MS } from "./k6/limits.js";
import { p95ByName, readJson } from "./summary.mjs";

const DEFAULT_OUT = join(dirname(fileURLToPath(import.meta.url)), "baseline.json");

function parseArgs(argv) {
  const args = { out: DEFAULT_OUT, measuredOn: "", commit: "", summaries: [] };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--out") args.out = argv[(i += 1)];
    else if (argv[i] === "--measured-on") args.measuredOn = argv[(i += 1)];
    else if (argv[i] === "--commit") args.commit = argv[(i += 1)];
    else args.summaries.push(argv[i]);
  }
  if (!args.measuredOn || !args.commit || args.summaries.length === 0) {
    console.error(
      "usage: write_baseline.mjs --measured-on <runner> --commit <sha> [--out <file>] <summary.json>...",
    );
    process.exit(2);
  }
  return args;
}

function main() {
  const { out, measuredOn, commit, summaries } = parseArgs(process.argv.slice(2));
  const worst = {};
  for (const path of summaries) {
    for (const [name, p95] of Object.entries(p95ByName(readJson(path)))) {
      worst[name] = Math.max(worst[name] ?? 0, p95);
    }
  }
  const missing = Object.keys(NFR_MS).filter((name) => !(name in worst));
  if (missing.length > 0) {
    console.error(`no p95 in the summaries for: ${missing.join(", ")}`);
    process.exit(2);
  }
  const baseline = Object.fromEntries(
    Object.keys(NFR_MS).map((name) => [name, { p95_ms: Math.round(worst[name] * 10) / 10 }]),
  );
  const body = { ...baseline, measured_on: measuredOn, commit, runs: summaries.length };
  writeFileSync(out, `${JSON.stringify(body, null, 2)}\n`);
  console.log(JSON.stringify(body, null, 2));
}

main();
