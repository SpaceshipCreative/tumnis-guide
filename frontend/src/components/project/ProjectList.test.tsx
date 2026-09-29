// The project list (P0-17, FR-2.1): projects with their health, and "New project"
// posting one idempotent create that opens the new project's page (A0.1 step 5).
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test("[P0-17][FR-2.1] lists projects with their health", async () => {
  const projects = [
    makeProject({ name: "Acme brand refresh", health: "blocked" }),
    makeProject({ name: "Tumnis dogfood", health: "on_track" }),
  ];
  server.use(
    http.get("/v1/projects", () =>
      HttpResponse.json({ items: projects, next_cursor: null }),
    ),
  );

  await renderRoute("/projects");

  expect(
    await screen.findByRole("link", { name: "Acme brand refresh" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Blocked")).toBeInTheDocument();
  expect(screen.getByText("On track")).toBeInTheDocument();
  expect(
    screen.getAllByRole("link", { name: "Projects" }).length,
  ).toBeGreaterThan(0);
});

test("[P0-17][FR-2.1] New project saves once and opens the project", async () => {
  const created = makeProject({ name: "Acme rebrand", client: "Acme" });
  const posts: { body: unknown; headers: Headers }[] = [];
  server.use(
    http.get("/v1/projects", () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
    http.post("/v1/projects", async ({ request }) => {
      posts.push({ body: await request.json(), headers: request.headers });
      return HttpResponse.json(created, { status: 201 });
    }),
    http.get(`/v1/projects/${created.id}`, () => HttpResponse.json(created)),
  );

  const { router, user } = await renderRoute("/projects");
  await user.click(await screen.findByRole("button", { name: "New project" }));
  await user.type(screen.getByLabelText("Name"), "Acme rebrand");
  await user.type(screen.getByLabelText("Client"), "Acme");
  await user.type(screen.getByLabelText("Goal"), "Ship the new logo");
  await user.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => {
    expect(router.state.location.pathname).toBe(`/projects/${created.id}`);
  });
  expect(
    await screen.findByRole("heading", { level: 1, name: "Acme rebrand" }),
  ).toBeInTheDocument();
  expect(posts).toHaveLength(1);
  expect(posts[0]?.body).toEqual({
    name: "Acme rebrand",
    client: "Acme",
    goal: "Ship the new logo",
  });
  expect(posts[0]?.headers.get("Idempotency-Key")).toBeTruthy();
});
