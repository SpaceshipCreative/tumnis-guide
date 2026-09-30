// Quick-add round trip (P0-29, T-P0-29-04; NFR Performance, PERF-2): the project
// typeahead, then POST /v1/tasks with an API key, at 5 captures a second for 30 s on the
// load set (the plan says 60 s; 30 s keeps the Performance job inside its 5 minutes). The p95 of each stays under min(NFR, the
// regression limit of perf/k6/limits.js: 20% and at least 50 ms over perf/baseline.json).
//
//   k6 run -e BASE_URL=http://localhost:8080 -e TUMNIS_KEY=... -e PROJECT_ID=... \
//     --summary-export quickadd.json perf/k6/quickadd.js
//
// Rate: two requests per capture is 10 a second, the per-principal limit's refill
// (burst 50), so the bucket never runs dry at an even arrival rate.
import { check } from "k6";
import http from "k6/http";

import { baseUrl, headers, p95Threshold } from "./lib.js";

export const options = {
  scenarios: {
    quick_add: {
      executor: "constant-arrival-rate",
      rate: 5,
      timeUnit: "1s",
      duration: "30s",
      preAllocatedVUs: 10,
    },
  },
  thresholds: {
    "http_req_duration{name:quick_add}": [p95Threshold("quick_add")],
    "http_req_duration{name:typeahead}": [p95Threshold("typeahead")],
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  const base = baseUrl();
  // The load set's projects are "Load project 01" to "10".
  const typed = http.get(`${base}/v1/typeahead/projects?q=lo`, {
    headers: headers(),
    tags: { name: "typeahead" },
  });
  check(typed, { "typeahead 200": (r) => r.status === 200 });
  const created = http.post(
    `${base}/v1/tasks`,
    JSON.stringify({ project_id: __ENV.PROJECT_ID, title: `k6 ${crypto.randomUUID()}` }),
    {
      headers: { ...headers(), "Idempotency-Key": crypto.randomUUID() },
      tags: { name: "quick_add" },
    },
  );
  check(created, { "created 201": (r) => r.status === 201 });
}
