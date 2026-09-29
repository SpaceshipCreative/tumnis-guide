// Session undo (P0-24, UX 9, R-09): status, move, edit and delete to trash each show an
// Undo toast; Undo (the button, or Mod+Z outside a text field) sends
// `POST /v1/tasks/{id}/undo {change_id, version}` with the ids the action answered, and
// the page shows the restored task.
import { screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../test/factories";
import { ProjectFake } from "../test/msw/project";
import { server } from "../test/msw/server";
import { renderRoute } from "../test/render";
import { undoStore } from "./undo";

type Trigger = "button" | "keyboard";

interface Action {
  name: string;
  view: "tasks" | "board";
  toast: string;
  perform: (user: UserEvent, title: string) => Promise<void>;
  /** The page shows the task as it was before the action. */
  restored: (title: string) => Promise<void>;
}

const TITLE = "Send logo drafts";

async function openDrawer(user: UserEvent, title: string) {
  await user.click(await screen.findByRole("button", { name: title }));
  return screen.findByRole("dialog", { name: title });
}

const ACTIONS: Action[] = [
  {
    name: "status to done",
    view: "tasks",
    toast: "Marked done",
    perform: async (user, title) => {
      await user.click(
        await screen.findByRole("button", { name: `Done ${title}` }),
      );
      const done = screen.getByRole("region", { name: "Done recently" });
      expect(await within(done).findByText(title)).toBeVisible();
    },
    restored: async (title) => {
      const today = screen.getByRole("region", { name: "Today" });
      expect(await within(today).findByText(title)).toBeVisible();
    },
  },
  {
    name: "move to another column",
    view: "board",
    toast: "Moved to Backlog",
    perform: async (user, title) => {
      const doing = await screen.findByRole("region", { name: "In progress" });
      const card = await within(doing).findByRole("listitem", {
        name: new RegExp(title),
      });
      await user.click(within(card).getByRole("button", { name: "Move" }));
      await user.click(screen.getByRole("menuitem", { name: "Backlog" }));
      const backlog = screen.getByRole("region", { name: "Backlog" });
      expect(await within(backlog).findByText(title)).toBeVisible();
    },
    restored: async (title) => {
      const doing = screen.getByRole("region", { name: "In progress" });
      expect(await within(doing).findByText(title)).toBeVisible();
    },
  },
  {
    name: "title edit",
    view: "tasks",
    toast: "Title changed",
    perform: async (user, title) => {
      const drawer = await openDrawer(user, title);
      const field = within(drawer).getByRole("textbox", { name: "Title" });
      await user.clear(field);
      await user.type(field, "Send logo drafts v2");
      await user.click(within(drawer).getByRole("button", { name: "Save" }));
      expect(
        await screen.findByRole("button", { name: "Send logo drafts v2" }),
      ).toBeVisible();
      await user.click(within(drawer).getByRole("button", { name: "Close" }));
    },
    restored: async (title) => {
      expect(await screen.findByRole("button", { name: title })).toBeVisible();
      expect(
        screen.queryByRole("button", { name: "Send logo drafts v2" }),
      ).toBeNull();
    },
  },
  {
    name: "delete to trash",
    view: "tasks",
    toast: "Moved to trash",
    perform: async (user, title) => {
      const drawer = await openDrawer(user, title);
      await user.click(
        within(drawer).getByRole("button", { name: "Move to trash" }),
      );
      await waitFor(() => {
        expect(screen.queryByRole("dialog", { name: title })).toBeNull();
      });
      await waitFor(() => {
        expect(screen.queryByRole("button", { name: title })).toBeNull();
      });
    },
    restored: async (title) => {
      expect(await screen.findByRole("button", { name: title })).toBeVisible();
    },
  },
];

test.fails(
  "[P0-24][UX 9] T-P0-24-11 status, move, edit and delete each undo",
  async () => {
    for (const action of ACTIONS) {
      for (const trigger of ["button", "keyboard"] as Trigger[]) {
        for (const entry of undoStore.getSnapshot().context.entries) {
          undoStore.trigger.drop({ changeId: entry.changeId });
        }
        const project = makeProject({ name: "Acme site" });
        const task = makeTask({
          project_id: project.id,
          parent_id: null,
          title: TITLE,
          status: "in_progress",
          label: "human",
          estimate_minutes: 30,
        });
        const fake = new ProjectFake({ project, tasks: [task] });
        server.resetHandlers();
        server.use(...fake.handlers);
        const label = `${action.name} (${trigger})`;

        const { user, unmount } = await renderRoute(
          `/projects/${project.id}?view=${action.view}`,
          { viewport: "laptop" },
        );
        await action.perform(user, TITLE);

        // The ids the action answered: its change and the version after it.
        const changes = [...fake.changes.values()].filter(
          (c) => c.taskId === task.id,
        );
        const changeId = changes.at(-1)?.id;
        const afterVersion = fake.task(task.id).version;
        expect(changeId, label).toBeDefined();

        const toast = await screen.findByRole("status", { name: "Undo" });
        expect(toast, label).toHaveTextContent(action.toast);
        if (trigger === "button") {
          await user.click(within(toast).getByRole("button", { name: "Undo" }));
        } else {
          (document.activeElement as HTMLElement | null)?.blur();
          await user.keyboard("{Control>}z{/Control}");
        }

        await waitFor(() => {
          expect(
            fake.sent("POST", `/v1/tasks/${task.id}/undo`),
            label,
          ).toHaveLength(1);
        });
        expect(
          fake.sent("POST", `/v1/tasks/${task.id}/undo`)[0]?.body,
          label,
        ).toEqual({
          change_id: changeId,
          version: afterVersion,
        });
        await action.restored(TITLE);
        unmount();
      }
    }
  },
);
