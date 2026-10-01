// T-P1-17-04 (FR-15.1): "Make task" on a checklist item creates the task with one
// `POST /v1/tasks` that carries an idempotency key, then swaps the item's text for a
// mention of the new task: `- [ ] [Send invoice](tumnis://task/<id>)`.
import { Editor } from "@tiptap/core";
import { afterEach, expect, test } from "vitest";

import { TaskCreateFake } from "../test/msw/tasks";
import { server } from "../test/msw/server";
import { buildExtensions } from "./extensions";
import { makeTask } from "./makeTask";

const PROJECT = "0192f3a4-4444-7000-8000-000000000004";

let editor: Editor | null = null;
afterEach(() => {
  editor?.destroy();
  editor = null;
});

test.fails("[P1-17][FR-15.1] make task swaps in a mention", async () => {
  const fake = new TaskCreateFake();
  server.resetHandlers();
  server.use(...fake.handlers);
  editor = new Editor({
    extensions: buildExtensions({}),
    content: "- [ ] Send invoice\n- [ ] Draft sitemap\n",
    contentType: "markdown",
  });
  // The cursor in the first item, as after a click on it.
  editor.commands.setTextSelection(5);

  const taskId = await makeTask(editor, { projectId: PROJECT });

  expect(fake.recorder.sent).toHaveLength(1);
  expect(fake.recorder.sent[0]?.path).toBe("/v1/tasks");
  expect(fake.recorder.sent[0]?.body).toMatchObject({
    project_id: PROJECT,
    title: "Send invoice",
  });
  expect(fake.keys()).toHaveLength(1);
  expect(fake.keys()[0]).not.toBe("");
  const created = fake.created()[0];
  expect(taskId).toBe(created?.id);
  expect(editor.getMarkdown()).toBe(
    `- [ ] [Send invoice](tumnis://task/${String(taskId)})\n- [ ] Draft sitemap`,
  );
});
