// The acceptance seed (Scott decision 37). `POST /v1/test/reset?set=acceptance`
// loads the seed user with the rows the phase 1 and phase 2 journeys name, on
// Monday 2026-03-09. The A1.x and A2.x specs get this set from `seededApp` by
// their tags, and every other spec keeps the seed set.
import {
  expect,
  listTasks,
  projectIdByName,
  seedSetFor,
  test,
} from "../fixtures";

test(
  "T-SEED-11 the acceptance set holds the journeys' projects and tasks",
  { tag: ["@SEED", "@J3", "@J7"] },
  async ({ signedInPage: page, seededApp }) => {
    test.fail();
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
    test.fail();
    expect(seedSetFor(["@A1.1", "@J2"])).toBe("acceptance");
    expect(seedSetFor(["@A2.6", "@J8"])).toBe("acceptance");
    expect(seedSetFor(["@A1.5", "@FR-15.2"])).toBe("acceptance");
    expect(seedSetFor(["@A0.1", "@J2"])).toBe("seed");
    expect(seedSetFor(["@P0-23"])).toBe("seed");
    expect(seedSetFor([])).toBe("seed");
  },
);
