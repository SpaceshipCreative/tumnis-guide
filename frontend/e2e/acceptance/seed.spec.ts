// The acceptance seed (Scott decision 37). `POST /v1/test/reset?set=acceptance`
// loads the seed user with the rows the phase 1 and phase 2 journeys name, on
// Monday 2026-03-09. The A1.x and A2.x specs get this set from `seededApp` by
// their tags, and every other spec keeps the seed set.
import {
  expect,
  listTasks,
  projectIdByName,
  seedSetFor,
  setServerClock,
  test,
} from "../fixtures";
import {
  getPlan,
  getTask,
  MASTER,
  MONDAY,
  MONDAY_PLAN_TIME,
  runnerScript,
} from "../phase1";

test(
  "T-SEED-11 the acceptance set holds the journeys' projects and tasks",
  { tag: ["@SEED"] },
  async ({ signedInPage: page, seededApp }) => {
    await seededApp.reset("acceptance");

    for (const name of ["Acme site", "Beta app", "Gamma ops"]) {
      expect(await projectIdByName(page.request, name)).toBeTruthy();
    }
    const acme = await projectIdByName(page.request, "Acme site");
    const titles = (await listTasks(page.request, acme)).map((t) => t.title);
    expect(titles).toEqual(
      expect.arrayContaining([
        "Fix footer link",
        "Write Acme proposal",
        "Invoice Acme for phase one",
        "Send logo drafts to Acme",
      ]),
    );
  },
);

test(
  "T-SEED-12 acceptance specs reset to the acceptance set by their tags",
  { tag: ["@SEED"] },
  () => {
    expect(seedSetFor(["@A1.1", "@J2"])).toBe("acceptance");
    expect(seedSetFor(["@A2.6", "@J8"])).toBe("acceptance");
    expect(seedSetFor(["@A1.5", "@FR-15.2"])).toBe("acceptance");
    expect(seedSetFor(["@A0.1", "@J2"])).toBe("seed");
    expect(seedSetFor(["@P0-23"])).toBe("seed");
    expect(seedSetFor([])).toBe("seed");
  },
);

test(
  "T-SEED-21 one planner tick publishes the master's scripted Monday plan",
  { tag: ["@SEED"] },
  async ({ signedInPage: page, seededApp, fakes }) => {
    test.fail();
    // The compose.test stack has no runner daemon: the acceptance master, whose runner
    // never connected, is the worker's FakeAgent, which plays the stored script.
    await seededApp.reset("acceptance");
    await fakes.runner.script(
      runnerScript(MASTER, "plan", "plan__monday_four_picks"),
    );
    await setServerClock(page.request, MONDAY_PLAN_TIME);
    await fakes.tick("planner-tick");

    const plan = await getPlan(page.request, MONDAY);
    expect(plan.source).toBe("master");
    expect(plan.trigger).toBe("morning");
    expect(plan.notice).toBeNull();
    const ordered = [...plan.items].sort((a, b) => a.position - b.position);
    const titles: string[] = [];
    for (const item of ordered) {
      titles.push((await getTask(page.request, item.task_id)).title);
    }
    expect(titles).toEqual([
      "Invoice Acme for phase one",
      "Send logo drafts to Acme",
      "Record lesson one",
      "Generate March analytics report",
    ]);
  },
);
