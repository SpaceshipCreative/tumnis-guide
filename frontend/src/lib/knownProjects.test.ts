// The projects this device has seen (P0-25, FR-3.10): they survive a reload so quick add
// can pick one while offline, and a 401 forgets them.
import { QueryClient } from "@tanstack/react-query";
import { beforeEach, expect, test } from "vitest";

import { makeProject } from "../test/factories";
import {
  forgetProjects,
  KNOWN_PROJECTS_KEY,
  loadKnownProjects,
  rememberProjects,
  trackKnownProjects,
} from "./knownProjects";

beforeEach(() => {
  window.localStorage.clear();
});

test("remembers projects newest first and replaces one of the same id", () => {
  rememberProjects([{ id: "a", name: "Acme" }]);
  rememberProjects([
    { id: "b", name: "Beta" },
    { id: "a", name: "Acme renamed" },
  ]);
  expect(loadKnownProjects()).toEqual([
    { id: "b", name: "Beta" },
    { id: "a", name: "Acme renamed" },
  ]);
});

test("forgetting empties the list", () => {
  rememberProjects([{ id: "a", name: "Acme" }]);
  forgetProjects();
  expect(loadKnownProjects()).toEqual([]);
});

test("unreadable or oddly shaped storage reads as none", () => {
  window.localStorage.setItem(KNOWN_PROJECTS_KEY, "{not json");
  expect(loadKnownProjects()).toEqual([]);
  window.localStorage.setItem(KNOWN_PROJECTS_KEY, '{"id":"a"}');
  expect(loadKnownProjects()).toEqual([]);
  window.localStorage.setItem(
    KNOWN_PROJECTS_KEY,
    JSON.stringify([{ id: "a" }, { id: "b", name: "Beta", extra: 1 }, 7]),
  );
  expect(loadKnownProjects()).toEqual([{ id: "b", name: "Beta" }]);
});

test("a loaded project list and a loaded project are remembered", async () => {
  const client = new QueryClient();
  trackKnownProjects(client);
  const acme = makeProject({ name: "Acme" });
  const beta = makeProject({ name: "Beta" });
  await client.query({
    queryKey: [{ _id: "projectsListProjects" }],
    queryFn: () => ({ items: [acme], next_cursor: null }),
  });
  await client.query({
    queryKey: [{ _id: "projectsGetProject" }],
    queryFn: () => beta,
  });
  await client.query({
    queryKey: [{ _id: "tasksListTasks" }],
    queryFn: () => ({ items: [{ id: "t", name: "not a project" }] }),
  });
  expect(loadKnownProjects()).toEqual([
    { id: beta.id, name: "Beta" },
    { id: acme.id, name: "Acme" },
  ]);
});
