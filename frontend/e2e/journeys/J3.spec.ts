// A2.1 · Run an AI task end to end (journey J3). Phase 2 acceptance, committed red on
// the phase's first day. Turns green with P2-04 (with P2-02's packet and task token);
// P2-07 is not needed, the fake runner speaks protocol 2. The fake runner is scripted
// through `fakes.runner.script(taskTitle, runs)` (R-37) and read back through
// `fakes.runner.lastPacket()`.
import { expect, projectIdByName, taskCards, test } from "../fixtures";
import { ACME, taskDrawer } from "../phase1";
import {
  FEEDBACK,
  FIX_FOOTER,
  FOOTER_LINES,
  FOOTER_RUNS,
  commentBodies,
  filesTouched,
  packetErrors,
  reviewBadge,
  reviewBadgeCount,
  reviewCount,
  reviewItem,
  runElapsed,
  runLog,
  taskByTitle,
  taskStatus,
  untrustedBlocks,
} from "../phase2";

test(
  "A2.1 run an AI task: packet, streamed log, result review, reject and accept",
  {
    tag: ["@A2.1", "@J3", "@FR-5.4", "@FR-5.5", "@FR-5.8", "@P2-04"],
  },
  async ({ signedInPage: page, fakes }) => {
    test.fail();
    await fakes.runner.script(FIX_FOOTER, FOOTER_RUNS);
    const acme = await projectIdByName(page.request, ACME);
    const task = await taskByTitle(page.request, ACME, FIX_FOOTER);

    // 1. Open the project's tasks view, open the task drawer, press Run.
    await page.goto(`/projects/${acme}?view=tasks`);
    await taskCards(page, FIX_FOOTER)
      .first()
      .getByText(FIX_FOOTER, { exact: true })
      .click();
    const drawer = taskDrawer(page, FIX_FOOTER);
    await drawer.getByRole("button", { name: "Run", exact: true }).click();

    // 2. The drawer switches to the run view; the three log lines arrive.
    await expect(page).toHaveURL(
      new RegExp(`[?&]task=${task.id}(&.*)?[&]run=[0-9a-f-]{36}`),
    );
    const firstRun = new URL(page.url()).searchParams.get("run") ?? "";

    // The runner received exactly one `run`: a valid packet with a task token and the
    // email inside one untrusted block.
    await expect
      .poll(async () => (await fakes.runner.lastPacket()).runMessages)
      .toBe(1);
    const { packet } = await fakes.runner.lastPacket();
    expect(packetErrors(packet)).toEqual([]);
    expect(packet.run_id).toBe(firstRun);
    const callback = packet.callback as { task_token?: string } | null;
    expect(callback?.task_token).toMatch(/^tmt_/);
    expect(untrustedBlocks(String(packet.prompt_text))).toBe(1);

    // The run view: the three lines in order, elapsed time ticking, the touched file,
    // a Stop button.
    const log = runLog(drawer);
    await expect(log.getByRole("listitem")).toContainText([...FOOTER_LINES]);
    const elapsed = runElapsed(drawer);
    const shown = await elapsed.innerText();
    await expect(elapsed).not.toHaveText(shown);
    await expect(filesTouched(drawer)).toContainText("src/footer.tsx");
    await expect(drawer.getByRole("button", { name: "Stop" })).toBeVisible();

    // After the first result the task is In review and its review item shows the
    // summary, the files touched and both links.
    await expect
      .poll(() => taskStatus(page.request, task.id))
      .toBe("in_review");

    // 3. The review badge on the dashboard; the result item; Reject with feedback.
    await page.goto("/");
    await reviewBadge(page).click();
    const item = reviewItem(page, FIX_FOOTER);
    await item.click();
    await expect(item).toContainText("Fixed the footer link target");
    await expect(filesTouched(item)).toContainText("src/footer.tsx");
    await expect(
      item.getByRole("link", { name: "fix-footer-link" }),
    ).toHaveAttribute(
      "href",
      "https://git.example.com/acme/site/tree/fix-footer-link",
    );
    await expect(
      item.getByRole("link", { name: "Pull request 7" }),
    ).toHaveAttribute("href", "https://git.example.com/acme/site/pull/7");
    await item.getByRole("button", { name: "Reject" }).click();
    await page.getByRole("textbox", { name: "Feedback" }).fill(FEEDBACK);
    await page.getByRole("button", { name: "Confirm reject" }).click();

    // After Reject: In progress, a comment with the feedback, and a second run whose
    // packet carries that comment.
    await expect
      .poll(() => taskStatus(page.request, task.id))
      .toBe("in_progress");
    expect(await commentBodies(page.request, task.id)).toContainEqual(
      expect.stringContaining(FEEDBACK),
    );
    await expect
      .poll(async () => (await fakes.runner.lastPacket()).runMessages)
      .toBe(2);
    const second = (await fakes.runner.lastPacket()).packet;
    expect(second.run_id).not.toBe(firstRun);
    expect(String(second.prompt_text)).toContain(FEEDBACK);

    // 4. The second run's result arrives; open the review item again; Accept.
    await expect
      .poll(() => taskStatus(page.request, task.id))
      .toBe("in_review");
    await page.goto("/");
    const before = await reviewCount(page.request);
    await expect(reviewBadgeCount(page)).toHaveText(String(before));
    await reviewBadge(page).click();
    const again = reviewItem(page, FIX_FOOTER);
    await again.click();
    await expect(again).toContainText("The footer link now opens in a new tab");
    await again.getByRole("button", { name: "Accept" }).click();

    // After Accept: Done, and the review badge dropped by one.
    await expect.poll(() => taskStatus(page.request, task.id)).toBe("done");
    await page.goto("/");
    await expect(reviewBadgeCount(page)).toHaveText(String(before - 1));
  },
);
