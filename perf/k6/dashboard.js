// The dashboard's reads and the typeahead (P0-29, T-P0-29-05; PERF-2): the project list,
// Today's first five tasks and the project typeahead, on the load set, 2 rounds a second
// for 20 s (the Performance job has 5 minutes). Each p95 stays under min(NFR, the
// regression limit of perf/k6/limits.js: 20% and at least 50 ms over perf/baseline.json).
//
//   k6 run -e BASE_URL=http://localhost:8080 -e TUMNIS_KEY=... \
//     --summary-export dashboard.json perf/k6/dashboard.js
//
// Rate: three requests per round is 6 a second, under the per-principal limit (10).
import { check } from "k6";
import http from "k6/http";

import { baseUrl, headers, p95Threshold } from "./lib.js";

export const options = {
  scenarios: {
    dashboard: {
      executor: "constant-arrival-rate",
      rate: 2,
      timeUnit: "1s",
      duration: "20s",
      preAllocatedVUs: 6,
    },
  },
  thresholds: {
    "http_req_duration{name:dashboard_projects}": [p95Threshold("dashboard_projects")],
    "http_req_duration{name:dashboard_today}": [p95Threshold("dashboard_today")],
    "http_req_duration{name:typeahead}": [p95Threshold("typeahead")],
    http_req_failed: ["rate<0.01"],
  },
};

function get(path, name) {
  const answer = http.get(`${baseUrl()}${path}`, { headers: headers(), tags: { name } });
  check(answer, { [`${name} 200`]: (r) => r.status === 200 });
}

export default function () {
  get("/v1/projects", "dashboard_projects");
  get("/v1/tasks?status=today&order=today&limit=5", "dashboard_today");
  get("/v1/typeahead/projects?q=lo", "typeahead");
}
