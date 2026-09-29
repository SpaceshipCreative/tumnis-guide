// Test-only hooks behind the Playwright `fakes` fixture (phase 1 acceptance, R-37).
// Mounted only with fake adapters (compose.test and previews; 404 otherwise):
// `POST /v1/test/tick/{schedule_name}` runs one scheduled workflow tick
// synchronously, `POST /v1/test/fakes/{adapter}/script` scripts a fake, and
// `POST /v1/test/fakes/runner/offline` takes a fake runner profile offline.
// The server clock is `setServerClock` in fixtures.ts. Each call throws on a
// non-2xx answer, so a hook that does not exist yet fails the test body (where
// `test.fail()` applies), never the fixture setup.
import type { APIRequestContext } from "@playwright/test";

type Json = Record<string, unknown>;

export interface TestFakes {
  /** `POST /v1/test/tick/{schedule}`, e.g. `planner-tick`. */
  tick(schedule: string): Promise<void>;
  /** `POST /v1/test/fakes/{adapter}/script`, e.g. `decisions.jev`, `generation`. */
  script(adapter: string, script: Json): Promise<void>;
  readonly runner: {
    /** `POST /v1/test/fakes/runner/script`: a scripted answer for (profile, skill). */
    script(script: Json): Promise<void>;
    /** `POST /v1/test/fakes/runner/offline`: the profile stops answering. */
    offline(profile: string): Promise<void>;
  };
}

async function post(
  request: APIRequestContext,
  path: string,
  data?: Json,
): Promise<void> {
  const response = await request.post(path, data === undefined ? {} : { data });
  if (!response.ok()) {
    throw new Error(`POST ${path} -> ${String(response.status())}`);
  }
}

export function testFakes(request: APIRequestContext): TestFakes {
  const script = (adapter: string, body: Json): Promise<void> =>
    post(request, `/v1/test/fakes/${encodeURIComponent(adapter)}/script`, body);
  return {
    tick: (schedule) =>
      post(request, `/v1/test/tick/${encodeURIComponent(schedule)}`),
    script,
    runner: {
      script: (body) => script("runner", body),
      offline: (profile) =>
        post(request, "/v1/test/fakes/runner/offline", { profile }),
    },
  };
}
