// Quick add (P0-25, FR-3.3, FR-3.9): `/` opens it on any route (never while typing), the
// project is required and defaults to the project page it was opened on, and the project
// typeahead asks `GET /v1/typeahead/projects`.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { SearchHit } from "../../api/types.gen";
import { makeProject } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { accountHandlers, Recorder } from "../../test/msw/settings";
import { TaskCreateFake } from "../../test/msw/tasks";
import { renderRoute } from "../../test/render";

function projectHit(project: { id: string; name: string }): SearchHit {
  return {
    entity_id: project.id,
    entity_type: "project",
    project_id: project.id,
    score: 1,
    snippet: project.name,
    title: project.name,
  };
}

const dialog = () => screen.getByRole("dialog", { name: "Quick add" });
const noDialog = () => screen.queryByRole("dialog", { name: "Quick add" });

test.fails(
  "[P0-25][FR-3.3] T-P0-25-01 slash opens quick-add on any route",
  async () => {
    const project = makeProject({ name: "Acme site" });
    server.use(
      ...new ProjectFake({ project }).handlers,
      ...accountHandlers(new Recorder()),
    );

    for (const url of ["/", `/projects/${project.id}`, "/settings/account"]) {
      const { user, unmount } = await renderRoute(url, { viewport: "laptop" });
      await user.keyboard("/");
      const open = await screen.findByRole("dialog", { name: "Quick add" });
      const title = within(open).getByRole("textbox", { name: "Title" });
      expect(title, url).toHaveFocus();
      expect(title, url).toHaveValue("");
      await user.keyboard("{Escape}");
      await waitFor(() => {
        expect(noDialog()).toBeNull();
      });
      unmount();
    }

    // Typing in a field: `/` is a slash there, not a shortcut.
    const { user } = await renderRoute(`/projects/${project.id}?view=tasks`, {
      viewport: "laptop",
    });
    const composer = await screen.findByRole("textbox", { name: "New task" });
    await user.click(composer);
    await user.keyboard("a/b");
    expect(composer).toHaveValue("a/b");
    expect(noDialog()).toBeNull();
  },
);

test.fails(
  "[P0-25][FR-3.3] T-P0-25-02 project is required and defaults to the current project",
  async () => {
    const acme = makeProject({ name: "Acme" });
    const creates = new TaskCreateFake();
    server.use(...new ProjectFake({ project: acme }).handlers);
    server.use(...creates.handlers);

    // On a project page the project field holds that project.
    const onProject = await renderRoute(`/projects/${acme.id}`, {
      viewport: "laptop",
    });
    await screen.findByRole("heading", { level: 1, name: "Acme" });
    await onProject.user.keyboard("/");
    await screen.findByRole("dialog", { name: "Quick add" });
    expect(
      within(dialog()).getByRole("combobox", { name: "Project" }),
    ).toHaveValue("Acme");
    await onProject.user.keyboard("{Escape}");
    onProject.unmount();

    // Elsewhere it is empty, and Enter without a project asks for one.
    const home = await renderRoute("/", { viewport: "laptop" });
    await home.user.keyboard("/");
    await screen.findByRole("dialog", { name: "Quick add" });
    const project = within(dialog()).getByRole("combobox", {
      name: "Project",
    });
    expect(project).toHaveValue("");
    await home.user.keyboard("Send logo drafts{Enter}");
    expect(dialog()).toBeVisible();
    expect(project).toHaveFocus();
    expect(within(dialog()).getByText("Pick a project")).toBeVisible();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(creates.recorder.sent).toEqual([]);
  },
);

test.fails(
  "[P0-25][FR-3.9] T-P0-25-03 project typeahead uses the search endpoint",
  async () => {
    const acme = makeProject({ name: "Acme rebrand" });
    const other = makeProject({ name: "Acme support" });
    const creates = new TaskCreateFake();
    const asked: string[] = [];
    server.use(
      http.get("/v1/typeahead/projects", ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("q") ?? "");
        return HttpResponse.json([projectHit(acme), projectHit(other)]);
      }),
      ...creates.handlers,
    );

    const { user } = await renderRoute("/", { viewport: "laptop" });
    await user.keyboard("/");
    await screen.findByRole("dialog", { name: "Quick add" });
    const title = within(dialog()).getByRole("textbox", { name: "Title" });
    await user.keyboard("Send logo drafts");
    const project = within(dialog()).getByRole("combobox", {
      name: "Project",
    });
    await user.type(project, "acm");

    const option = await screen.findByRole("option", { name: "Acme rebrand" });
    expect(screen.getByRole("option", { name: "Acme support" })).toBeVisible();
    // One request for what was typed, not one per keystroke.
    expect(asked).toEqual(["acm"]);
    await user.click(option);
    expect(project).toHaveValue("Acme rebrand");

    await user.click(title);
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(creates.bodies()).toEqual([
        { project_id: acme.id, title: "Send logo drafts" },
      ]);
    });
    expect(creates.recorder.sent[0]?.idempotencyKey).toBeTruthy();
    expect(noDialog()).toBeNull();
    expect(screen.getByText("Added to Acme rebrand")).toBeVisible();
  },
);
