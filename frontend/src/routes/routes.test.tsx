// Typed routes (P0-22, UX 11): search params and path params can never be invalid.
// A bad value falls back to its default; a bad path lands somewhere real.
import { waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import { reviewKinds } from "../test/msw/handlers";
import { server } from "../test/msw/server";
import { renderRoute } from "../test/render";

test("[P0-22][UX 11] T-P0-22-01 invalid project search params fall back", async () => {
  const projectId = crypto.randomUUID();
  const { router } = await renderRoute(
    `/projects/${projectId}?view=bogus&task=nope&filter=7`,
  );

  expect(router.state.location.pathname).toBe(`/projects/${projectId}`);
  const leaf = router.state.matches.at(-1);
  expect(leaf?.routeId).toBe("/projects/$projectId");
  expect(leaf?.status).toBe("success");
  expect(leaf?.error).toBeUndefined();
  expect(leaf?.search).toEqual({});
});

test("[P0-22][UX 11] T-P0-22-02 non-uuid project id lands on the dashboard", async () => {
  const { router } = await renderRoute("/projects/not-a-uuid?view=board");

  await waitFor(() => {
    expect(router.state.location.pathname).toBe("/");
  });
  expect(router.state.matches.at(-1)?.routeId).toBe("/");
});

test("[P0-22][UX 11] T-P0-22-03 unknown settings section lands on account", async () => {
  const { router } = await renderRoute("/settings/nope");

  await waitFor(() => {
    expect(router.state.location.pathname).toBe("/settings/account");
  });
  expect(router.state.matches.at(-1)?.params).toEqual({
    section: "account",
  });
});

test("[P0-22][UX 11] T-P0-22-04 unknown path lands on the dashboard", async () => {
  const { router } = await renderRoute("/no/such/place");

  await waitFor(() => {
    expect(router.state.location.pathname).toBe("/");
  });
  expect(router.state.matches.at(-1)?.routeId).toBe("/");
});

test("[P0-22][UX 11] T-P0-22-19 an unregistered review kind is dropped", async () => {
  server.use(reviewKinds(["project_match", "estimate_outlier"]));

  const unknown = await renderRoute("/review?kind=nope");
  await waitFor(() => {
    expect(unknown.router.state.location.pathname).toBe("/review");
  });
  await waitFor(() => {
    expect(unknown.router.state.location.search).toEqual({});
  });
  unknown.unmount();

  const known = await renderRoute("/review?kind=project_match");
  expect(known.router.state.location.pathname).toBe("/review");
  expect(known.router.state.matches.at(-1)?.search).toEqual({
    kind: "project_match",
  });
});
