// Global search (P0-25, FR-3.9): Mod+K (Ctrl or Cmd + K) opens the search palette on
// every screen; it asks `GET /v1/search` and each task result links to its task.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { PageSearchHit } from "../../api/types.gen";
import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { accountHandlers, Recorder } from "../../test/msw/settings";
import { renderRoute } from "../../test/render";

const noPalette = () => screen.queryByRole("dialog", { name: "Search" });

test.fails(
  "[P0-25][FR-3.9] T-P0-25-04 Mod+K opens search on every screen and results link to tasks",
  async () => {
    const project = makeProject({ name: "Acme site" });
    const task = makeTask({
      project_id: project.id,
      title: "Send logo drafts",
    });
    const asked: string[] = [];
    const results: PageSearchHit = {
      items: [
        {
          entity_id: task.id,
          entity_type: "task",
          project_id: project.id,
          score: 2,
          snippet: "Send <b>logo</b> drafts",
          title: task.title,
        },
        {
          entity_id: project.id,
          entity_type: "project",
          project_id: project.id,
          score: 1,
          snippet: project.name,
          title: project.name,
        },
      ],
      next_cursor: null,
    };
    server.use(
      ...new ProjectFake({ project, tasks: [task] }).handlers,
      ...accountHandlers(new Recorder()),
      http.get("/v1/search", ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("q") ?? "");
        return HttpResponse.json(results);
      }),
    );

    const taskHref = `/projects/${project.id}?task=${task.id}`;
    for (const url of ["/", `/projects/${project.id}`, "/settings/account"]) {
      const { user, unmount } = await renderRoute(url, { viewport: "laptop" });
      await user.keyboard("{Control>}k{/Control}");
      const palette = await screen.findByRole("dialog", { name: "Search" });
      const box = within(palette).getByRole("searchbox", { name: "Search" });
      expect(box, url).toHaveFocus();
      await user.keyboard("logo");
      const hit = await within(palette).findByRole("link", {
        name: /Send logo drafts/,
      });
      expect(hit, url).toHaveAttribute("href", taskHref);
      expect(
        within(palette).getByRole("link", { name: /Acme site/ }),
      ).toHaveAttribute("href", `/projects/${project.id}`);
      await user.keyboard("{Escape}");
      await waitFor(() => {
        expect(noPalette()).toBeNull();
      });
      unmount();
    }
    expect(asked.at(-1)).toBe("logo");

    // Cmd+K works too, and following a task result opens that task.
    const { user, router } = await renderRoute("/", { viewport: "laptop" });
    await user.keyboard("{Meta>}k{/Meta}");
    const palette = await screen.findByRole("dialog", { name: "Search" });
    await user.keyboard("logo");
    await user.click(
      await within(palette).findByRole("link", { name: /Send logo drafts/ }),
    );
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/projects/${project.id}`);
    });
    expect(router.state.location.search).toMatchObject({ task: task.id });
    expect(noPalette()).toBeNull();
  },
);
