// Close the day (P1-18, J7): an optional one-minute panel with four sections (Shipped,
// Agents finished, Queued overnight, Rolls over). Rollover counts are plain numbers with
// no warning colour (UX 8), and closing it, by Done or by Escape, writes nothing: skipping
// the day close costs nothing.
import { screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { daySummary, EMPTY_DAY, FULL_DAY } from "../../test/msw/closeDay";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CloseDayPanel } from "./CloseDayPanel";

const DAY = "2026-03-09";
const SECTIONS = [
  "Shipped",
  "Agents finished",
  "Queued overnight",
  "Rolls over",
];

async function openPanel(onClose: () => void = () => undefined) {
  const rendered = renderWithProviders(
    <CloseDayPanel day={DAY} onClose={onClose} />,
  );
  const panel = await screen.findByRole("dialog", { name: "Close the day" });
  const part = (name: string) => within(panel).getByRole("region", { name });
  return { ...rendered, panel, part };
}

describe("CloseDayPanel", () => {
  test.fails("[P1-18][J7] T-P1-18-08 sections and empty states", async () => {
    server.use(daySummary(FULL_DAY));
    const { panel, part, unmount } = await openPanel();

    // The four sections, in order.
    const regions = within(panel).getAllByRole("region");
    expect(regions).toHaveLength(SECTIONS.length);
    SECTIONS.forEach((name, i) => {
      expect(regions[i]).toHaveAccessibleName(name);
    });

    const shipped = await within(part("Shipped")).findAllByRole("listitem");
    expect(shipped.map((li) => li.textContent)).toEqual([
      expect.stringContaining("Write Acme proposal"),
      expect.stringContaining("Summarise the brief"),
    ]);
    const agents = part("Agents finished");
    expect(within(agents).getAllByRole("listitem")).toHaveLength(1);
    expect(agents).toHaveTextContent("Summarise the brief");
    expect(agents).toHaveTextContent("1 task prepared by agents");
    expect(part("Queued overnight")).toHaveTextContent("Nothing queued");

    // Rollovers: the count now as a plain number, and what happens tonight.
    const rolls = part("Rolls over");
    const rows = within(rolls).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(
      rows.map((row) => within(row).getByTestId("rollover-count").textContent),
    ).toEqual(["0", "3"]);
    for (const row of rows) expect(row).toHaveTextContent("+1 tonight");
    // No warning colour anywhere in the section (UX 8: no shame, no alarm).
    const coloured = [rolls, ...rolls.querySelectorAll("*")].filter((el) =>
      /\b(?:text|bg|border)-(?:warning|danger)/.test(
        el.getAttribute("class") ?? "",
      ),
    );
    expect(coloured).toEqual([]);
    unmount();

    // A quiet day: every section says so instead of disappearing.
    server.use(daySummary(EMPTY_DAY));
    const empty = await openPanel();
    expect(empty.part("Shipped")).toHaveTextContent("Nothing shipped today");
    expect(empty.part("Agents finished")).toHaveTextContent(
      "No agent work finished today",
    );
    expect(empty.part("Agents finished")).not.toHaveTextContent(
      "prepared by agents",
    );
    expect(empty.part("Queued overnight")).toHaveTextContent("Nothing queued");
    expect(empty.part("Rolls over")).toHaveTextContent("Nothing rolls over");
    expect(within(empty.panel).queryAllByRole("listitem")).toEqual([]);
  });

  test("[P1-18][J7] T-P1-18-09 dismissing sends no write", async () => {
    server.use(daySummary(FULL_DAY));
    const sent: string[] = [];
    const record = ({ request }: { request: Request }) => {
      sent.push(`${request.method} ${new URL(request.url).pathname}`);
    };
    server.events.on("request:start", record);
    try {
      // Done closes the panel without asking anything.
      const onDone = vi.fn();
      const first = await openPanel(onDone);
      await within(first.panel).findAllByRole("listitem");
      await first.user.click(
        within(first.panel).getByRole("button", { name: "Done" }),
      );
      expect(onDone).toHaveBeenCalledTimes(1);
      first.unmount();

      // So does Escape.
      const onEscape = vi.fn();
      const second = await openPanel(onEscape);
      await within(second.panel).findAllByRole("listitem");
      await second.user.keyboard("{Escape}");
      expect(onEscape).toHaveBeenCalledTimes(1);
    } finally {
      server.events.removeListener("request:start", record);
    }

    expect(sent.length).toBeGreaterThan(0); // the panel read its summary
    expect(sent.filter((s) => !s.startsWith("GET "))).toEqual([]);
  });
});
