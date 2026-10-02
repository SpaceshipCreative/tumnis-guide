// The Search page (P0-22's route, FR-3.9). APP-02 (application test): /search was an
// empty placeholder with only its heading. It searches now: a box bound to `?q=`, the
// scope (`?scope=`), and the results of `GET /v1/search` linking to each task or project.
// Every fixture is synthetic.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { SearchHit } from "../api/types.gen";
import { server } from "../test/msw/server";
import { renderRoute } from "../test/render";

const PROJECT_ID = "0192b3c4-0000-7000-8000-0000000000a1";
const TASK_ID = "0192b3c4-0000-7000-8000-0000000000b1";

const TASK_HIT: SearchHit = {
  entity_id: TASK_ID,
  entity_type: "task",
  project_id: PROJECT_ID,
  score: 0.9,
  snippet: "Fix the footer link on the pricing page",
  title: "Fix footer link",
};
const PROJECT_HIT: SearchHit = {
  entity_id: PROJECT_ID,
  entity_type: "project",
  project_id: null,
  score: 0.5,
  snippet: "",
  title: "Acme site",
};

function searchApi() {
  const asked: URLSearchParams[] = [];
  return {
    asked,
    handler: http.get("*/v1/search", ({ request }) => {
      const params = new URL(request.url).searchParams;
      asked.push(params);
      const q = params.get("q") ?? "";
      const scope = params.get("scope") ?? "all";
      if (params.get("cursor") === "page-2") {
        return HttpResponse.json({ items: [PROJECT_HIT], next_cursor: null });
      }
      if (q === "footer") {
        return HttpResponse.json({
          items: scope === "projects" ? [] : [TASK_HIT],
          next_cursor: scope === "all" ? "page-2" : null,
        });
      }
      return HttpResponse.json({ items: [], next_cursor: null });
    }),
  };
}

test("[P0-24][FR-3.9] APP-02 /search?q= shows the results and links each one", async () => {
  const api = searchApi();
  server.use(api.handler);
  const { user } = await renderRoute("/search?q=footer", { viewport: "phone" });

  expect(screen.getByRole("heading", { name: "Search" })).toBeVisible();
  expect(screen.getByRole("searchbox")).toHaveValue("footer");
  expect(screen.getByRole("radio", { name: "All" })).toBeChecked();

  const results = await screen.findByRole("list", { name: "Results" });
  const task = within(results).getByRole("link", { name: /Fix footer link/ });
  expect(task).toHaveAttribute(
    "href",
    `/projects/${PROJECT_ID}?task=${TASK_ID}`,
  );
  expect(
    within(task).getByText("Fix the footer link on the pricing page"),
  ).toBeVisible();
  expect(api.asked[0]?.get("q")).toBe("footer");
  expect(api.asked[0]?.get("scope")).toBe("all");

  await user.click(screen.getByRole("button", { name: "Load more" }));
  const project = await within(results).findByRole("link", {
    name: /Acme site/,
  });
  expect(project).toHaveAttribute("href", `/projects/${PROJECT_ID}`);
  expect(api.asked.at(-1)?.get("cursor")).toBe("page-2");
});

test("[P0-24][FR-3.9] APP-02 typing and the scope update the URL and the results", async () => {
  const api = searchApi();
  server.use(api.handler);
  const { router, user } = await renderRoute("/search", { viewport: "laptop" });

  expect(screen.getByRole("searchbox")).toHaveValue("");
  expect(api.asked).toHaveLength(0);

  await user.type(screen.getByRole("searchbox"), "footer");
  await waitFor(() => {
    expect(router.state.location.search).toEqual({
      q: "footer",
      scope: "all",
    });
  });
  expect(
    await screen.findByRole("link", { name: /Fix footer link/ }),
  ).toBeVisible();

  await user.click(screen.getByRole("radio", { name: "Projects" }));
  await waitFor(() => {
    expect(router.state.location.search).toEqual({
      q: "footer",
      scope: "projects",
    });
  });
  expect(await screen.findByText("Nothing found")).toBeVisible();
  expect(api.asked.at(-1)?.get("scope")).toBe("projects");
});
