// The latency limits behind the k6 thresholds and perf/check_summary.mjs (P0-29, PERF-2).
// A plain ES module with no k6 or Node imports, so k6 (perf/k6/lib.js) and Node
// (perf/check_summary.mjs, perf/thresholds.test.mjs) share one rule.

// NFR: quick-add 500 ms (PRD Performance); the dashboard reads are (plan default) API
// shares of the 1 s first paint; the typeahead is the quick-add typeahead budget.
export const NFR_MS = {
  quick_add: 500,
  dashboard_projects: 300,
  dashboard_today: 300,
  typeahead: 100,
};

// PERF-2 and Scott decision 27: a latency fails only when it is more than 20% worse AND
// at least 50 ms worse than the committed baseline, so small latencies don't fail on noise.
export const REGRESSION = 1.2;
export const REGRESSION_FLOOR_MS = 50;

/**
 * The p95 limit in ms for `name`: max(1.2 x its baseline p95, its baseline p95 + 50 ms),
 * never above its NFR. A p95 at or over the limit fails. Below a 250 ms baseline the
 * 50 ms floor is the larger; above it the 20%.
 */
export function limitMs(name, baseline) {
  const nfr = NFR_MS[name];
  if (nfr === undefined) throw new Error(`no NFR for ${name}`);
  const p95 = baseline?.[name]?.p95_ms;
  if (typeof p95 !== "number" || !(p95 > 0)) {
    throw new Error(`the baseline has no p95_ms for ${name}`);
  }
  const regression = Math.max(Math.round(p95 * REGRESSION), Math.round(p95 + REGRESSION_FLOOR_MS));
  return Math.min(nfr, regression);
}

/** The k6 threshold expression for `name`, for example `p(95)<120`. */
export function threshold(name, baseline) {
  return `p(95)<${String(limitMs(name, baseline))}`;
}
