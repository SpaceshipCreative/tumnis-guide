// Archive and unarchive in the UI (P2-18, FR-2.1, FR-5.10). APP-13 (archive UI,
// application test): the API had `POST /v1/projects/{id}/archive` and `/unarchive`, but
// no screen called them. A project's Settings section archives it (after asking) and
// unarchives it, its header says it is archived, and the project list has an archived
// projects list with Unarchive (P2-18's done checklist).
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { ProjectOut } from "../../api/types.gen";
import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderRoute, type Viewport } from "../../test/render";

type Project = ReturnType<typeof makeProject>;

/** The archive routes over a project held here, so a refetch answers the latest. */
function archiveRoutes(start: Project) {
  const recorder = new Recorder();
  let current: Project = start;
  const handlers = [
    http.get(`/v1/projects/${start.id}`, () => HttpResponse.json(current)),
    http.post(`/v1/projects/${start.id}/archive`, async ({ request }) => {
      await recorder.record(request);
      current = {
        ...current,
        archived_at: "2026-03-09T12:00:00Z",
        archive_state: "archiving",
        version: current.version + 1,
      };
      return HttpResponse.json(current);
    }),
    http.post(`/v1/projects/${start.id}/unarchive`, async ({ request }) => {
      await recorder.record(request);
      current = {
        ...current,
        archived_at: null,
        archive_state: null,
        version: current.version + 1,
      };
      return HttpResponse.json(current);
    }),
  ];
  return { recorder, handlers };
}

async function archivesFromSettings(viewport: Viewport): Promise<void> {
  const project = makeProject({
    name: "Beta app",
    version: 3,
    archived_at: null,
  });
  const task = makeTask({ project_id: project.id, title: "Ship it" });
  const fake = new ProjectFake({ project, tasks: [task] });
  const { recorder, handlers } = archiveRoutes(project);
  server.resetHandlers();
  server.use(...handlers, ...fake.handlers);

  const { user } = await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport,
  });
  await screen.findByRole("heading", { level: 1, name: "Beta app" });
  if (viewport === "phone") {
    await user.click(screen.getByRole("button", { name: "Context" }));
  }
  const settings = await screen.findByRole("button", { name: /^Settings/ });
  if (settings.getAttribute("aria-expanded") !== "true") {
    await user.click(settings);
  }

  // Archive asks first; Cancel sends nothing.
  await user.click(
    await screen.findByRole("button", { name: "Archive project" }),
  );
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(recorder.sent).toHaveLength(0);

  await user.click(screen.getByRole("button", { name: "Archive project" }));
  await user.click(screen.getByRole("button", { name: "Yes, archive" }));
  await waitFor(() => {
    expect(recorder.sent).toHaveLength(1);
  });
  const [archive] = recorder.sent;
  expect(archive?.method).toBe("POST");
  expect(archive?.path).toBe(`/v1/projects/${project.id}/archive`);
  expect(archive?.idempotencyKey).toBeTruthy();
  expect(archive?.body).toEqual({ version: 3 });

  // The header says so, and Settings offers Unarchive.
  expect(
    await screen.findByRole("img", { name: "Archive: archiving" }),
  ).toBeVisible();
  await user.click(
    await screen.findByRole("button", { name: "Unarchive project" }),
  );
  await waitFor(() => {
    expect(recorder.sent).toHaveLength(2);
  });
  expect(recorder.sent[1]?.path).toBe(`/v1/projects/${project.id}/unarchive`);
  expect(recorder.sent[1]?.body).toEqual({ version: 4 });
  expect(
    await screen.findByRole("button", { name: "Archive project" }),
  ).toBeVisible();
  expect(screen.queryByRole("img", { name: /^Archive:/ })).toBeNull();
}

test("[P2-18][FR-2.1] APP-13 (archive UI) archive and unarchive from project Settings at 375 px", async () => {
  await archivesFromSettings("phone");
});

test("[P2-18][FR-2.1] APP-13 (archive UI) archive and unarchive from project Settings at 1280 px", async () => {
  await archivesFromSettings("laptop");
});

async function unarchivesFromList(viewport: Viewport): Promise<void> {
  const active = makeProject({ name: "Live site" });
  let archived = {
    ...makeProject({ name: "Old site", version: 7 }),
    archived_at: "2026-03-01T09:00:00Z",
    archive_state: "archived",
  } as ProjectOut;
  const recorder = new Recorder();
  const reads: string[] = [];
  server.use(
    http.get("/v1/projects", ({ request }) => {
      const url = new URL(request.url);
      reads.push(url.search);
      const all = url.searchParams.get("include_archived") === "true";
      const items = [active, archived].filter(
        (p) => all || p.archived_at === null,
      );
      return HttpResponse.json({ items, next_cursor: null });
    }),
    http.post(`/v1/projects/${archived.id}/unarchive`, async ({ request }) => {
      await recorder.record(request);
      archived = {
        ...archived,
        archived_at: null,
        archive_state: null,
        version: archived.version + 1,
      };
      return HttpResponse.json(archived);
    }),
  );

  const { user } = await renderRoute("/projects", { viewport });
  expect(
    await screen.findByRole("link", { name: "Live site" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Old site" })).toBeNull();
  // The archived list is asked for only when it is opened.
  expect(reads.some((search) => search.includes("include_archived"))).toBe(
    false,
  );

  const toggle = screen.getByRole("button", {
    name: "Show archived projects",
  });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await user.click(toggle);
  const list = await screen.findByRole("list", { name: "Archived projects" });
  expect(
    await within(list).findByRole("link", { name: "Old site" }),
  ).toBeVisible();
  expect(within(list).queryByRole("link", { name: "Live site" })).toBeNull();

  await user.click(
    within(list).getByRole("button", { name: "Unarchive Old site" }),
  );
  await waitFor(() => {
    expect(recorder.sent).toHaveLength(1);
  });
  expect(recorder.sent[0]?.body).toEqual({ version: 7 });
  expect(recorder.sent[0]?.idempotencyKey).toBeTruthy();

  // Back in the project list, gone from the archived one.
  await waitFor(() => {
    expect(within(list).queryByRole("link", { name: "Old site" })).toBeNull();
  });
  expect(within(list).getByText("No archived projects.")).toBeVisible();
  expect(screen.getByRole("link", { name: "Old site" })).toBeVisible();
}

test("[P2-18][FR-5.10] APP-13 (archive UI) the archived projects list unarchives at 375 px", async () => {
  await unarchivesFromList("phone");
});

test("[P2-18][FR-5.10] APP-13 (archive UI) the archived projects list unarchives at 1280 px", async () => {
  await unarchivesFromList("laptop");
});
