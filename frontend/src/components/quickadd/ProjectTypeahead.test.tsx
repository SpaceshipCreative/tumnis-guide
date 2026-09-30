// Where the project picker finds projects besides the search endpoint (P0-25, FR-3.10):
// the project list it loads itself, and the projects this device saw on earlier pages,
// so a page that holds none (opened offline) still offers them.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, test } from "vitest";

import { rememberProjects } from "../../lib/knownProjects";
import { makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { account } from "../../test/msw/settings";
import { renderRoute } from "../../test/render";

beforeEach(() => {
  window.localStorage.clear();
});

async function typeProject(text: string) {
  // The account section's own read (issue #76: every request has a handler).
  server.use(http.get("/v1/auth/account", () => HttpResponse.json(account())));
  const { user } = await renderRoute("/settings/account", {
    viewport: "laptop",
  });
  await user.keyboard("/");
  const dialog = await screen.findByRole("dialog", { name: "Quick add" });
  await user.type(
    within(dialog).getByRole("combobox", { name: "Project" }),
    text,
  );
}

test("finds a project from the project list when search knows none", async () => {
  const acme = makeProject({ name: "Acme rebrand" });
  server.use(
    http.get("/v1/typeahead/projects", () => HttpResponse.json([])),
    http.get("/v1/projects", () =>
      HttpResponse.json({ items: [acme], next_cursor: null }),
    ),
  );
  await typeProject("acm");
  expect(
    await screen.findByRole("option", { name: "Acme rebrand" }),
  ).toBeVisible();
});

test("finds a project this device saw earlier when nothing loads", async () => {
  rememberProjects([
    { id: "01900000-0000-7000-8000-00000000a0a0", name: "Acme rebrand" },
  ]);
  server.use(
    http.get("/v1/typeahead/projects", () => HttpResponse.error()),
    http.get("/v1/projects", () => HttpResponse.error()),
  );
  await typeProject("acm");
  expect(
    await screen.findByRole("option", { name: "Acme rebrand" }),
  ).toBeVisible();
});
