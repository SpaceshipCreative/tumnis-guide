// P4-05 · A push deep-links to its review item (FR-8.3). A push carries
// `/review?kind=<kind>&item=<id>` (notifications.rules.deep_link_for); the service
// worker's click opens exactly that path. Opening it lands on the review queue with that
// item focused, at phone width (and at laptop width). The review item is a real one: the
// fake runner asks A2.2's question, which queues a `question` item.
import type { APIRequestContext } from "@playwright/test";

import { expect, test } from "../fixtures";
import { ACME } from "../phase1";
import {
  FIX_FOOTER,
  QUESTION,
  QUESTION_RUNS,
  runTask,
  taskByTitle,
  taskStatus,
} from "../phase2";

interface ReviewRow {
  readonly id: string;
  readonly kind: string;
  readonly payload: Record<string, unknown>;
}

async function questionItem(request: APIRequestContext): Promise<ReviewRow> {
  const response = await request.get("/v1/review?kind=question");
  expect(response.ok(), "GET /v1/review?kind=question").toBe(true);
  const { items } = (await response.json()) as { items: ReviewRow[] };
  const hits = items.filter((i) =>
    JSON.stringify(i.payload).includes(QUESTION),
  );
  expect(hits).toHaveLength(1);
  const [hit] = hits;
  if (hit === undefined) throw new Error(`no question item: ${QUESTION}`);
  return hit;
}

test(
  "T-P4-05-10 opening a push's review url lands on the item with focus",
  { tag: ["@P4-05", "@FR-8.3"] },
  async ({ signedInPage: page, seededApp, fakes }) => {
    await seededApp.reset("acceptance");
    await fakes.runner.script(FIX_FOOTER, QUESTION_RUNS);
    const task = await taskByTitle(page.request, ACME, FIX_FOOTER);
    await runTask(page.request, task.id);
    await expect
      .poll(() => taskStatus(page.request, task.id))
      .toBe("waiting_on_human");
    const item = await questionItem(page.request);

    // The URL a push for this item carries, opened as the service worker opens it.
    await page.goto(`/review?kind=${item.kind}&item=${item.id}`);
    const focused = page.locator(":focus");
    await expect(focused).toContainText(QUESTION);
    await expect(focused).toBeInViewport();
    await expect(page).toHaveURL(
      new RegExp(`/review\\?kind=question&item=${item.id}$`),
    );
  },
);
