// The dashboard route (P0-23, UX 1, FR-1.1, FR-1.2): the loader fills the cache before the
// first render, so the page opens with its data and never shows a spinner.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import {
  projectsList,
  reviewCount,
  todayTasks,
} from "../../test/msw/dashboard";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test.fails(
  "[P0-23][UX 1][FR-1.1] T-P0-23-08 first render has data, no spinner",
  async () => {
    const acme = makeProject({ name: "Acme site", health: "blocked" });
    const dogfood = makeProject({ name: "Tumnis dogfood", health: "on_track" });
    const queries: URLSearchParams[] = [];
    server.use(
      projectsList([acme, dogfood]),
      todayTasks(
        [
          makeTask({
            project_id: acme.id,
            title: "Send logo drafts",
            status: "today",
          }),
        ],
        1,
        (query) => queries.push(query),
      ),
      reviewCount(2),
    );

    await renderRoute("/", { viewport: "laptop" });

    // Synchronous queries: the data is there on the first render, not after a fetch.
    expect(screen.getByRole("link", { name: "Acme site" })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Tumnis dogfood" }),
    ).toBeInTheDocument();
    const today = screen.getByRole("region", { name: "Today" });
    expect(within(today).getByText("Send logo drafts")).toBeInTheDocument();
    expect(within(today).getByText("Acme site")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "2 to review" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/loading/i)).toBeNull();

    expect(queries).toHaveLength(1);
    expect(Object.fromEntries(queries[0] ?? [])).toEqual({
      status: "today",
      order: "today",
      limit: "5",
    });
  },
);
