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

// PERF-2: a latency more than 20% worse than the committed baseline fails.
export const REGRESSION = 1.2;

/** The p95 limit in ms for `name`: 1.2 x its baseline p95, never above its NFR. */
export function limitMs(name, baseline) {
  const nfr = NFR_MS[name];
  if (nfr === undefined) throw new Error(`no NFR for ${name}`);
  const p95 = baseline?.[name]?.p95_ms;
  if (typeof p95 !== "number" || !(p95 > 0)) {
    throw new Error(`the baseline has no p95_ms for ${name}`);
  }
  return Math.min(nfr, Math.round(p95 * REGRESSION));
}

/** The k6 threshold expression for `name`, for example `p(95)<120`. */
export function threshold(name, baseline) {
  return `p(95)<${String(limitMs(name, baseline))}`;
}
