// Shared k6 pieces (P0-29): the committed baseline, the thresholds built from it and the
// request headers. Thresholds: https://grafana.com/docs/k6/latest/using-k6/thresholds/
import { threshold } from "./limits.js";

// open() works only in the init context; the path is relative to this file.
const baseline = JSON.parse(open("../baseline.json"));

/** `p(95)<limit` for the requests tagged `name` (PERF-2, decision 27: more than 20% and at least 50 ms worse fails). */
export function p95Threshold(name) {
  return threshold(name, baseline);
}

/** The app under test (BASE_URL), without a trailing slash. */
export function baseUrl() {
  return (__ENV.BASE_URL || "http://localhost:8080").replace(/\/$/, "");
}

/** The API key (TUMNIS_KEY) as a bearer token, and JSON. */
export function headers() {
  return {
    Authorization: `Bearer ${__ENV.TUMNIS_KEY}`,
    "Content-Type": "application/json",
  };
}
