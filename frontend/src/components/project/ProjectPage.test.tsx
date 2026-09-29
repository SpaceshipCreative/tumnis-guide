// The project page's layout and view state (P0-24, FR-2.6, FR-2.8): tabs and a right rail
// on a laptop, a segmented control and a Context sheet on the phone; the last view per
// project comes back after a reload, and a throwing localStorage costs nothing but that.
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

function seeded() {
  const project = makeProject({ name: "Acme site" });
  const fake = new ProjectFake({
    project,
    tasks: [
      makeTask({
        project_id: project.id,
        title: "Send logo drafts",
        status: "backlog",
      }),
    ],
    brief: "Logo refresh for Acme",
  });
  server.use(...fake.handlers);
  return { project, fake };
}

beforeEach(() => {
  localStorage.clear();
});

const realStorage = Object.getOwnPropertyDescriptor(window, "localStorage");
afterEach(() => {
  if (realStorage) Object.defineProperty(window, "localStorage", realStorage);
});

test.fails(
  "[P0-24][FR-2.8] T-P0-24-07 phone shows a segmented control and a Context sheet",
  async () => {
    const { project } = seeded();

    const phone = await renderRoute(`/projects/${project.id}`, {
      viewport: "phone",
    });
    const views = await screen.findByRole("radiogroup", { name: "Views" });
    expect(within(views).getByRole("radio", { name: "Tasks" })).toBeChecked();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByRole("complementary", { name: "Context" })).toBeNull();
    await phone.user.click(screen.getByRole("button", { name: "Context" }));
    const sheet = screen.getByRole("dialog", { name: "Context" });
    for (const name of ["Brief", "Connections", "Schedule", "Settings"]) {
      expect(
        within(sheet).getByRole("button", { name: new RegExp(`^${name}`) }),
      ).toBeVisible();
    }
    await phone.user.click(within(views).getByRole("radio", { name: "Board" }));
    expect(
      await screen.findByRole("region", { name: "Backlog" }),
    ).toBeVisible();
    phone.unmount();

    const laptop = await renderRoute(`/projects/${project.id}?view=tasks`, {
      viewport: "laptop",
    });
    const tabs = await screen.findByRole("tablist", { name: "Views" });
    expect(within(tabs).getByRole("tab", { name: "Tasks" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByRole("radiogroup")).toBeNull();
    const rail = screen.getByRole("complementary", { name: "Context" });
    expect(within(rail).getByRole("button", { name: /^Brief/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Context" })).toBeNull();
    laptop.unmount();
  },
);

test.fails(
  "[P0-24][FR-2.6] T-P0-24-08 last view per project survives a reload",
  async () => {
    const { project } = seeded();
    const first = await renderRoute(`/projects/${project.id}`, {
      viewport: "laptop",
    });
    expect(
      await screen.findByRole("region", { name: "Up next" }),
    ).toBeVisible();
    await first.user.click(screen.getByRole("tab", { name: "Board" }));
    expect(
      await screen.findByRole("region", { name: "Backlog" }),
    ).toBeVisible();
    expect(JSON.parse(localStorage.getItem("tumnis.lastView") ?? "{}")).toEqual(
      {
        [project.id]: "board",
      },
    );
    first.unmount();

    // A reload: the store module loads again from localStorage.
    vi.resetModules();
    const { renderRoute: again } = await import("../../test/render");
    await again(`/projects/${project.id}`, { viewport: "laptop" });
    expect(
      await screen.findByRole("region", { name: "Backlog" }),
    ).toBeVisible();
    expect(screen.getByRole("tab", { name: "Board" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  },
);

test.fails(
  "[P0-24][FR-2.6] T-P0-24-09 a throwing localStorage falls back to Tasks",
  async () => {
    const { project } = seeded();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("denied", "SecurityError");
      },
    });
    vi.resetModules();
    const { renderRoute: fresh } = await import("../../test/render");

    const { user } = await fresh(`/projects/${project.id}`, {
      viewport: "laptop",
    });
    expect(
      await screen.findByRole("region", { name: "Up next" }),
    ).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Board" }));
    expect(
      await screen.findByRole("region", { name: "Backlog" }),
    ).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Tasks" }));
    expect(
      await screen.findByRole("region", { name: "Up next" }),
    ).toBeVisible();
  },
);
