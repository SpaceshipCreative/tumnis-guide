// A1.5 · PDF with a table to packet, Playwright part. Phase 1 acceptance,
// committed red on the phase's first day. The compose.test stack runs the
// Docling fake returning the stored chunks of
// backend/fixtures/extraction/rate-card-table.pdf. Turns green with P1-14
// (storage), P1-16 (scan and extraction) and P1-17 (Knowledge rail and packet
// preview); the `test.fail()` comes off when P1-17, the last of them, merges.
// The integration part is backend/tests/acceptance/test_a1_5_pdf_to_packet.py.
import { expect, projectIdByName, test } from "../fixtures";
import {
  ACME,
  createTask,
  dropFixture,
  knowledgeRail,
  projectComposer,
  taskDrawer,
} from "../phase1";

const RATE_CARD = "rate-card-table.pdf";
const TASK = "Quote Acme for the redesign";

test(
  "A1.5 dropped PDF is scanned, extracted and shows in the packet preview",
  {
    tag: ["@A1.5", "@FR-15.2", "@FR-15.3", "@FR-15.4", "@SEC-10", "@P1-17"],
  },
  async ({ signedInPage: page }) => {
    test.fail();
    const acme = await projectIdByName(page.request, ACME);
    const { id: taskId } = await createTask(page.request, ACME, TASK);
    await page.goto(`/projects/${acme}`);
    const rail = knowledgeRail(page);
    const items = rail.getByRole("listitem");
    const before = await items.count();

    // 1. Drop the PDF on the composer: the rail gains one item, Scanning then
    // Ready.
    await dropFixture(projectComposer(page), RATE_CARD, "application/pdf");
    await expect(items).toHaveCount(before + 1);
    const item = items.filter({ hasText: /rate.card/i });
    await expect(item).toContainText("Scanning");
    await expect(item).toContainText("Ready", { timeout: 60_000 });

    // 2. The task drawer's Packet preview: the brief first, then the passage
    // cited as `rate-card-table.pdf, page 2`.
    await page.goto(`/projects/${acme}?task=${taskId}`);
    const drawer = taskDrawer(page, TASK);
    await drawer.getByRole("button", { name: "Packet preview" }).click();
    const preview = drawer.getByRole("region", { name: "Packet preview" });
    const parts = preview.getByRole("article");
    await expect(parts.first()).toContainText("Brief");
    const passage = parts.filter({ hasText: "rate-card-table.pdf, page 2" });
    await expect(passage).toHaveCount(1);
    await expect(passage).toContainText("Senior designer");
    const order = await parts.allInnerTexts();
    const briefAt = order.findIndex((t) => t.includes("Brief"));
    const passageAt = order.findIndex((t) =>
      t.includes("rate-card-table.pdf, page 2"),
    );
    expect(briefAt).toBe(0);
    expect(passageAt).toBeGreaterThan(briefAt);
  },
);
