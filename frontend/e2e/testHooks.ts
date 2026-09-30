// Test-only hooks behind the Playwright `fakes` fixture (phase 1 acceptance, R-37).
// Mounted only with fake adapters (compose.test and previews; 404 otherwise):
// `POST /v1/test/tick/{schedule_name}` runs one scheduled workflow tick
// synchronously, `POST /v1/test/fakes/{adapter}/script` scripts a fake, and
// `POST /v1/test/fakes/runner/offline` takes a fake runner profile offline.
// Phase 2 (P2-04): `runner.script(taskTitle, runs)` scripts what the fake runner does
// for each run of that task (stream lines, artifacts, questions, then a result), and
// `GET /v1/test/fakes/runner/last-packet` answers the last `run` packet it received.
// The server clock is `setServerClock` in fixtures.ts. Each call throws on a
// non-2xx answer, so a hook that does not exist yet fails the test body (where
// `test.fail()` applies), never the fixture setup.
import type { APIRequestContext } from "@playwright/test";

type Json = Record<string, unknown>;

export interface LastPacket {
  readonly packet: Json;
  readonly runMessages: number;
}

export interface TestFakes {
  /** `POST /v1/test/tick/{schedule}`, e.g. `planner-tick`. */
  tick(schedule: string): Promise<void>;
  /** `POST /v1/test/fakes/{adapter}/script`, e.g. `decisions.jev`, `generation`. */
  script(adapter: string, script: Json): Promise<void>;
  readonly runner: {
    /** `POST /v1/test/fakes/runner/script`: a scripted answer for (profile, skill). */
    script(script: Json): Promise<void>;
    /**
     * `POST /v1/test/fakes/runner/script` (phase 2, R-37): the steps of each run of the
     * task titled `taskTitle`, one list per run in order (the first run plays `runs[0]`).
     */
    script(
      taskTitle: string,
      runs: readonly (readonly Json[])[],
    ): Promise<void>;
    /**
     * `GET /v1/test/fakes/runner/last-packet` (P2-04): the last `run` packet the fake
     * runner received, and how many `run` messages it received since the reset.
     */
    lastPacket(): Promise<LastPacket>;
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
  function runnerScript(script: Json): Promise<void>;
  function runnerScript(
    taskTitle: string,
    runs: readonly (readonly Json[])[],
  ): Promise<void>;
  function runnerScript(
    first: Json | string,
    runs?: readonly (readonly Json[])[],
  ): Promise<void> {
    return typeof first === "string"
      ? script("runner", { task_title: first, runs: runs ?? [] })
      : script("runner", first);
  }
  const lastPacket = async (): Promise<LastPacket> => {
    const path = "/v1/test/fakes/runner/last-packet";
    const response = await request.get(path);
    if (!response.ok()) {
      throw new Error(`GET ${path} -> ${String(response.status())}`);
    }
    const body = (await response.json()) as {
      packet: Json;
      run_messages: number;
    };
    return { packet: body.packet, runMessages: body.run_messages };
  };
  return {
    tick: (schedule) =>
      post(request, `/v1/test/tick/${encodeURIComponent(schedule)}`),
    script,
    runner: {
      script: runnerScript,
      lastPacket,
      offline: (profile) =>
        post(request, "/v1/test/fakes/runner/offline", { profile }),
    },
  };
}
