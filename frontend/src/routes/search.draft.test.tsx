// The Search box keeps what is typed while its own search updates the URL (P0-24, FR-3.9;
// CodeRabbit on #158). The box text belonged to the `q` it was typed against, so the `q`
// the box's own pause in typing wrote looked like an outside change: "fix " became "fix"
// and typing on gave "fixfooter". Every fixture is synthetic.
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../test/msw/server";
import { renderRoute } from "../test/render";

test("[P0-24][FR-3.9] the box keeps a trailing space after its own search updates the URL", async () => {
  server.use(
    http.get("*/v1/search", () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
  );
  const { router, user } = await renderRoute("/search", { viewport: "laptop" });
  const box = screen.getByRole("searchbox");

  await user.type(box, "fix ");
  await waitFor(() => {
    expect(router.state.location.search).toEqual({ q: "fix", scope: "all" });
  });
  // Past another typing pause: the URL write must not take the space back.
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(box).toHaveValue("fix ");

  await user.type(box, "footer");
  await waitFor(() => {
    expect(router.state.location.search).toEqual({
      q: "fix footer",
      scope: "all",
    });
  });
  expect(box).toHaveValue("fix footer");
});

test("[P0-24][FR-3.9] the box keeps its text when the scope changes", async () => {
  server.use(
    http.get("*/v1/search", () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
  );
  const { router, user } = await renderRoute("/search?q=fix", {
    viewport: "phone",
  });
  const box = screen.getByRole("searchbox");

  // The scope is picked before the typing pause: its search carries the new text.
  await user.type(box, "es ");
  await user.click(screen.getByRole("radio", { name: "Projects" }));
  await waitFor(() => {
    expect(router.state.location.search).toEqual({
      q: "fixes",
      scope: "projects",
    });
  });
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(box).toHaveValue("fixes ");
});
