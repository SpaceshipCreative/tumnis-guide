// A2.2 · A question mid-run resumes the run, UI path. Phase 2 acceptance, committed red
// on the phase's first day. Turns green with P2-05. The API-level part is
// backend/tests/acceptance/test_a2_2_question_resumes_run.py. The fake runner is
// scripted through `fakes.runner.script(taskTitle, runs)` (R-37): one log line, then
// `ask_human` ("Which footer color?"), then a result that echoes the answer.
import { expect, projectIdByName, taskCards, test } from "../fixtures";
import { ACME } from "../phase1";
import {
  FIX_FOOTER,
  QUESTION,
  QUESTION_RUNS,
  reviewBadgeCount,
  reviewCount,
  reviewItem,
  runTask,
  taskByTitle,
  taskStatus,
} from "../phase2";

test(
  "A2.2 a question mid-run shows on the review badge and the answer resumes the run",
  { tag: ["@A2.2", "@FR-5.7", "@P2-05"] },
  async ({ signedInPage: page, fakes }) => {
    await fakes.runner.script(FIX_FOOTER, QUESTION_RUNS);
    const task = await taskByTitle(page.request, ACME, FIX_FOOTER);
    await runTask(page.request, task.id);
    await expect
      .poll(() => taskStatus(page.request, task.id))
      .toBe("waiting_on_human");

    // The question appears on the dashboard's review badge.
    await page.goto("/");
    const open = await reviewCount(page.request);
    expect(open).toBeGreaterThan(0);
    await expect(reviewBadgeCount(page)).toHaveText(String(open));

    // And in /review?kind=question, where the answer is given.
    await page.goto("/review?kind=question");
    const item = reviewItem(page, QUESTION);
    await expect(item).toHaveCount(1);
    await item.getByRole("textbox", { name: "Answer" }).fill("Navy");
    await item.getByRole("button", { name: "Answer" }).click();

    // The task card returns to In progress.
    await expect
      .poll(() => taskStatus(page.request, task.id))
      .toBe("in_progress");
    const acme = await projectIdByName(page.request, ACME);
    await page.goto(`/projects/${acme}?view=tasks`);
    await expect(taskCards(page, FIX_FOOTER).first()).toContainText(
      "In progress",
    );
  },
);
