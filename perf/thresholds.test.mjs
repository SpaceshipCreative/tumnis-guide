// T-P0-29-06 · The k6 threshold builder and the summary check (P0-29, PERF-2): a latency
// more than 20% worse than the committed baseline fails the build, and the NFR caps the
// limit. Run: node --test perf/thresholds.test.mjs
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

import { limitMs, threshold } from "./k6/limits.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const CHECK = join(HERE, "check_summary.mjs");

const BASELINE = {
  quick_add: { p95_ms: 100 },
  dashboard_projects: { p95_ms: 100 },
  dashboard_today: { p95_ms: 100 },
  typeahead: { p95_ms: 100 },
  measured_on: "test",
  commit: "0000000",
};

// A k6 --summary-export file holding one tagged duration trend.
function summary(name, p95) {
  return {
    metrics: {
      [`http_req_duration{name:${name}}`]: { avg: p95 / 2, "p(95)": p95 },
      http_req_failed: { value: 0 },
    },
  };
}

function check(p95) {
  const root = mkdtempSync(join(tmpdir(), "k6-summary-"));
  const baseline = join(root, "baseline.json");
  const export_ = join(root, "summary.json");
  writeFileSync(baseline, JSON.stringify(BASELINE));
  writeFileSync(export_, JSON.stringify(summary("quick_add", p95)));
  return spawnSync(
    process.execPath,
    [CHECK, "--baseline", baseline, export_],
    { encoding: "utf8" },
  );
}

test("[P0-29][PERF-2] T-P0-29-06 a 25% slower summary fails the build", () => {
  // The builder: 1.2 x the baseline p95, under the NFR.
  assert.equal(limitMs("quick_add", BASELINE), 120);
  assert.equal(threshold("quick_add", BASELINE), "p(95)<120");

  // The check script on a k6 summary: 125 ms is 25% worse and fails; 118 ms passes.
  const slower = check(125);
  assert.equal(slower.status, 1, slower.stdout + slower.stderr);
  assert.match(slower.stdout, /quick_add/);
  const within = check(118);
  assert.equal(within.status, 0, within.stdout + within.stderr);

  // An NFR lower than 1.2 x the baseline wins: typeahead's NFR is 100 ms.
  const slow = { ...BASELINE, typeahead: { p95_ms: 95 } };
  assert.equal(limitMs("typeahead", slow), 100);
  assert.equal(limitMs("quick_add", { quick_add: { p95_ms: 450 } }), 500);
});
