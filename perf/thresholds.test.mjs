// T-P0-29-06 · The k6 threshold builder and the summary check (P0-29, PERF-2): a latency
// fails the build only when it is more than 20% worse AND at least 50 ms worse than the
// committed baseline (Scott decision 27), and the NFR caps the limit.
// Run: node --test perf/thresholds.test.mjs
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
  quick_add: { p95_ms: 300 },
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

function check(name, p95) {
  const root = mkdtempSync(join(tmpdir(), "k6-summary-"));
  const baseline = join(root, "baseline.json");
  const export_ = join(root, "summary.json");
  writeFileSync(baseline, JSON.stringify(BASELINE));
  writeFileSync(export_, JSON.stringify(summary(name, p95)));
  return spawnSync(
    process.execPath,
    [CHECK, "--baseline", baseline, export_],
    { encoding: "utf8" },
  );
}

test("[P0-29][PERF-2] T-P0-29-06 a 25% slower summary fails the build", () => {
  // Above a 250 ms baseline the 20% decides: 1.2 x 300 = 360 (over 300 + 50), under the NFR.
  assert.equal(limitMs("quick_add", BASELINE), 360);
  assert.equal(threshold("quick_add", BASELINE), "p(95)<360");

  // The check script on a k6 summary: 375 ms is 25% and 75 ms worse and fails; 355 passes.
  const slower = check("quick_add", 375);
  assert.equal(slower.status, 1, slower.stdout + slower.stderr);
  assert.match(slower.stdout, /quick_add/);
  const within = check("quick_add", 355);
  assert.equal(within.status, 0, within.stdout + within.stderr);

  // Below 250 ms the 50 ms decides: a 100 ms baseline allows up to 150 ms, so 125 ms
  // (25% but only 25 ms worse) passes and 150 ms (50% and 50 ms worse) fails.
  assert.equal(limitMs("dashboard_projects", BASELINE), 150);
  assert.equal(threshold("dashboard_projects", BASELINE), "p(95)<150");
  const noisy = check("dashboard_projects", 125);
  assert.equal(noisy.status, 0, noisy.stdout + noisy.stderr);
  const regressed = check("dashboard_projects", 150);
  assert.equal(regressed.status, 1, regressed.stdout + regressed.stderr);

  // An NFR lower than the regression limit wins: typeahead's NFR is 100 ms.
  const slow = { ...BASELINE, typeahead: { p95_ms: 95 } };
  assert.equal(limitMs("typeahead", slow), 100);
  assert.equal(limitMs("quick_add", { quick_add: { p95_ms: 450 } }), 500);
});
